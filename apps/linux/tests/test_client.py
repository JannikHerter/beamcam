import datetime
import hashlib
import os
import socket
import ssl
import struct
import tempfile
import threading
import time
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from beamcam import protocol, secure
from beamcam.client import CONNECT_FAILED, FORGOTTEN, UNKNOWN_PHONE, Session, parse_address


def _make_certificate(directory):
    """A self-signed EC certificate like the phone's keystore one."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Beamcam")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem")
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


_DIR = tempfile.TemporaryDirectory()
PHONE_TLS, PHONE_FP = _make_certificate(_DIR.name)
SECRET = bytes(range(32))


class MemoryPairings:
    def __init__(self, known=None):
        self.secrets = dict(known or {})
        self.paired = []
        self.connections = []

    def secret_for(self, fingerprint):
        return self.secrets.get(fingerprint)

    def pair(self, fingerprint, secret, phone_id, name):
        self.secrets[fingerprint] = secret
        self.paired.append((fingerprint, phone_id, name))

    def forget(self, fingerprint):
        self.secrets.pop(fingerprint, None)

    def connected(self, fingerprint, name):
        self.connections.append((fingerprint, name))


class Recorder:
    def __init__(self):
        self.events = []
        self.done = threading.Event()

    def on_pending(self, code):
        self.events.append(("pending", code))

    def on_accept(self, info):
        self.events.append(("accept", info.device_name, info.width, info.height))

    def on_frame(self, frame):
        self.events.append(("frame", frame.keyframe, frame.data))

    def on_camera(self, rotation, facing):
        self.events.append(("camera", rotation, facing))

    def on_end(self, reason):
        self.events.append(("end", reason))
        self.done.set()


def fake_phone(script, hold_open=False):
    """A TLS phone that serves one connection: read HELLO, send the scripted messages.
    `received` gets the HELLO, or "hello" stays missing if the computer sent none."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    received = {"later": []}

    def run():
        raw, _ = server.accept()
        try:
            conn = PHONE_TLS.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            raw.close()
            server.close()
            return
        with conn, conn.makefile("rb") as stream:
            try:
                received["hello"] = protocol.read_message(stream).json()
            except (EOFError, OSError):  # the computer hung up without a HELLO
                server.close()
                return
            for chunk in script:
                conn.sendall(chunk)
            if hold_open:
                try:
                    while True:
                        received["later"].append(protocol.read_message(stream))
                except (EOFError, OSError, ssl.SSLError):
                    pass
        server.close()

    threading.Thread(target=run, daemon=True).start()
    return server.getsockname()[1], received


def accept(**extra):
    body = {"protocol": 2, "deviceId": "phone-1", "deviceName": "Pixel", "codec": "h264", "width": 1280, "height": 720, "fps": 30}
    body.update(extra)
    return protocol.encode_json(protocol.ACCEPT, body)


END_STOPPED = protocol.encode_json(protocol.END, {"reason": "stopped"})


def run_session(port, pairings, allow_pairing=False, wait=True):
    recorder = Recorder()
    session = Session("127.0.0.1", port, "My PC", recorder, pairings, allow_pairing=allow_pairing)
    session.start()
    if wait:
        assert recorder.done.wait(5), "session did not end"
    return session, recorder


class PairingTest(unittest.TestCase):
    def test_first_connection_pairs_and_streams(self):
        port, received = fake_phone(
            [
                protocol.encode_json(protocol.PENDING, {}),
                accept(secret=SECRET.hex()),
                protocol.encode_json(protocol.CAMERA, {"rotation": 90, "facing": "back"}),
                protocol.encode(0x7F, b"from a newer phone"),
                protocol.encode_video(1, protocol.FLAG_KEYFRAME, b"\x00\x00\x00\x01\x65"),
                END_STOPPED,
            ]
        )
        pairings = MemoryPairings()
        session, recorder = run_session(port, pairings, allow_pairing=True)
        self.assertNotIn("secret", received["hello"])
        self.assertEqual(received["hello"]["protocol"], 2)
        self.assertEqual(
            recorder.events,
            [
                ("pending", secure.pairing_code(PHONE_FP)),
                ("accept", "Pixel", 1280, 720),
                ("camera", 90, "back"),
                ("frame", True, b"\x00\x00\x00\x01\x65"),
                ("end", "stopped"),
            ],
        )
        self.assertEqual(pairings.secrets, {PHONE_FP: SECRET})
        self.assertEqual(pairings.paired, [(PHONE_FP, "phone-1", "Pixel")])
        self.assertEqual(session.fingerprint, PHONE_FP)

    def test_paired_phone_gets_the_secret_and_no_prompt(self):
        port, received = fake_phone([accept(), END_STOPPED])
        pairings = MemoryPairings({PHONE_FP: SECRET})
        _, recorder = run_session(port, pairings)
        self.assertEqual(received["hello"]["secret"], SECRET.hex())
        self.assertEqual(recorder.events[-1], ("end", "stopped"))
        self.assertEqual(pairings.connections, [(PHONE_FP, "Pixel")])

    def test_unknown_phone_is_told_nothing_without_a_click(self):
        """A stranger advertising our phone's name must not even learn this computer's name."""
        port, received = fake_phone([accept(secret=SECRET.hex())])
        _, recorder = run_session(port, MemoryPairings({"another-phone": SECRET}))
        self.assertEqual(recorder.events, [("end", UNKNOWN_PHONE)])
        time.sleep(0.1)
        self.assertNotIn("hello", received)

    def test_phone_that_forgot_us_drops_the_pairing(self):
        port, _ = fake_phone([protocol.encode_json(protocol.END, {"reason": FORGOTTEN})])
        pairings = MemoryPairings({PHONE_FP: SECRET})
        _, recorder = run_session(port, pairings)
        self.assertEqual(recorder.events, [("end", FORGOTTEN)])
        self.assertEqual(pairings.secrets, {})

    def test_accept_without_secret_cannot_pair(self):
        port, _ = fake_phone([accept()])
        pairings = MemoryPairings()
        _, recorder = run_session(port, pairings, allow_pairing=True)
        self.assertEqual(recorder.events[-1], ("end", "protocol"))
        self.assertEqual(pairings.secrets, {})


class SessionTest(unittest.TestCase):
    def test_set_camera_from_another_thread(self):
        port, received = fake_phone([accept()], hold_open=True)
        session, recorder = run_session(port, MemoryPairings({PHONE_FP: SECRET}), wait=False)
        deadline = time.monotonic() + 5
        while not any(e[0] == "accept" for e in recorder.events) and time.monotonic() < deadline:
            time.sleep(0.01)
        session.set_camera("back")
        while not received["later"] and time.monotonic() < deadline:
            time.sleep(0.01)
        session.close()
        self.assertTrue(recorder.done.wait(5))
        self.assertEqual(received["later"][0].type, protocol.SET_CAMERA)
        self.assertEqual(received["later"][0].json(), {"facing": "back"})
        self.assertEqual(recorder.events[-1], ("end", "closed"))

    def test_unreachable_phone(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        _, recorder = run_session(port, MemoryPairings())
        self.assertEqual(recorder.events, [("end", CONNECT_FAILED)])

    def test_phone_closing_mid_stream_is_connection_lost(self):
        port, _ = fake_phone([accept()[:-2]])
        _, recorder = run_session(port, MemoryPairings({PHONE_FP: SECRET}))
        self.assertEqual(recorder.events[-1], ("end", "connection-lost"))


class UdpSessionTest(unittest.TestCase):
    """Fake phone: accepts, waits for the computer's first sealed FEEDBACK, then streams sealed video."""

    def start(self, key):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("127.0.0.1", 0))
        udp.settimeout(5)
        port, received = fake_phone([accept(udpPort=udp.getsockname()[1], udpKey=key.hex())], hold_open=True)
        recorder = Recorder()
        session = Session("127.0.0.1", port, "pc", recorder, MemoryPairings({PHONE_FP: SECRET}))
        session.start()
        self.addCleanup(udp.close)
        return udp, session, recorder, received

    def receive(self, udp, phone):
        """Next datagram from the computer, opened with the phone's key."""
        while True:
            data, computer = udp.recvfrom(2048)
            plaintext = phone.open(data)
            if plaintext is not None:
                return plaintext, computer

    @staticmethod
    def frames(recorder):
        return [e[2][0] for e in recorder.events if e[0] == "frame"]

    def wait_frames(self, recorder, count):
        deadline = time.monotonic() + 5
        while len(self.frames(recorder)) < count and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_sealed_video_with_keyframe_request_after_loss(self):
        key = bytes(range(32, 64))
        phone = secure.Sealer(key, secure.PHONE_TO_COMPUTER, secure.COMPUTER_TO_PHONE)
        udp, session, recorder, received = self.start(key)
        feedback, computer = self.receive(udp, phone)
        self.assertEqual(feedback[0], protocol.UDP_FEEDBACK)

        def send(frame_id, flags=0, drop=None):
            for i, part in enumerate(protocol.encode_fragments(frame_id, frame_id, flags, bytes([frame_id]) * 3000)):
                if i != drop:
                    udp.sendto(phone.seal(part), computer)

        # Garbage and datagrams sealed with another key are ignored.
        udp.sendto(b"\x01" * 100, computer)
        stranger = secure.Sealer(bytes(32), secure.PHONE_TO_COMPUTER, secure.COMPUTER_TO_PHONE)
        udp.sendto(stranger.seal(protocol.encode_fragments(9, 9, protocol.FLAG_KEYFRAME, b"x")[0]), computer)

        send(0, protocol.FLAG_KEYFRAME)
        send(1, drop=0)
        send(2)
        while True:  # this phone ignores NACKs, so the computer gives up on frame 1 and asks for a keyframe
            packet, _ = self.receive(udp, phone)
            if packet[0] == protocol.UDP_FEEDBACK and packet[1] & protocol.FEEDBACK_KEYFRAME:
                break
        send(3, protocol.FLAG_KEYFRAME)
        send(4)
        self.wait_frames(recorder, 3)
        session.close()
        self.assertTrue(recorder.done.wait(5))
        self.assertEqual(self.frames(recorder), [0, 3, 4])
        self.assertEqual(session.transport, "udp")
        self.assertIn("udp", received["hello"]["transports"])

    def test_nack_resend_avoids_keyframe(self):
        key = bytes(range(64, 96))
        phone = secure.Sealer(key, secure.PHONE_TO_COMPUTER, secure.COMPUTER_TO_PHONE)
        udp, session, recorder, _ = self.start(key)
        _, computer = self.receive(udp, phone)
        frames = {i: protocol.encode_fragments(i, i, protocol.FLAG_KEYFRAME if i == 0 else 0, bytes([i]) * 3000) for i in range(3)}
        for i, parts in frames.items():
            for j, part in enumerate(parts):
                if (i, j) != (1, 1):
                    udp.sendto(phone.seal(part), computer)
        while True:
            packet, _ = self.receive(udp, phone)
            self.assertFalse(packet[0] == protocol.UDP_FEEDBACK and packet[1] & protocol.FEEDBACK_KEYFRAME)
            if packet[0] == protocol.UDP_NACK:
                frame_id, index = struct.unpack_from(">IH", packet, 2)
                udp.sendto(phone.seal(frames[frame_id][index]), computer)
                break
        self.wait_frames(recorder, 3)
        session.close()
        recorder.done.wait(5)
        self.assertEqual(self.frames(recorder), [0, 1, 2])


class AddressTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_address("192.168.1.5"), ("192.168.1.5", protocol.DEFAULT_PORT))
        self.assertEqual(parse_address(" 192.168.1.5:5000 "), ("192.168.1.5", 5000))
        self.assertEqual(parse_address("[fe80::1]:5000"), ("fe80::1", 5000))
        self.assertEqual(parse_address("fe80::1"), ("fe80::1", protocol.DEFAULT_PORT))
        self.assertEqual(parse_address("pixel.local"), ("pixel.local", protocol.DEFAULT_PORT))

    def test_bad(self):
        for text in ("", ":80", "1.2.3.4:99999", "1.2.3.4:abc"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_address(text)


if __name__ == "__main__":
    unittest.main()
