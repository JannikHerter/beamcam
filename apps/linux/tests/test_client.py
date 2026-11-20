import socket
import struct
import threading
import time
import unittest

from openwebcam import protocol
from openwebcam.client import CONNECT_FAILED, Session, parse_address


class Recorder:
    def __init__(self):
        self.events = []
        self.done = threading.Event()

    def on_pending(self):
        self.events.append(("pending",))

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
    """Listen once, read HELLO, then send the scripted messages. Returns (port, received hello)."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    received = {}

    def run():
        conn, _ = server.accept()
        with conn, conn.makefile("rb") as stream:
            received["hello"] = protocol.read_message(stream).json()
            for chunk in script:
                conn.sendall(chunk)
            if hold_open:
                stream.read(1)  # until the computer disconnects
        server.close()

    threading.Thread(target=run, daemon=True).start()
    return server.getsockname()[1], received


END_STOPPED = protocol.encode_json(protocol.END, {"reason": "stopped"})


class SessionTest(unittest.TestCase):
    def test_handshake_and_stream(self):
        port, received = fake_phone(
            [
                protocol.encode_json(protocol.PENDING, {}),
                protocol.encode_json(
                    protocol.ACCEPT, {"protocol": 1, "deviceName": "Pixel", "codec": "h264", "width": 1280, "height": 720, "fps": 30}
                ),
                protocol.encode_json(protocol.CAMERA, {"rotation": 90, "facing": "back"}),
                protocol.encode(0x7F, b"from a newer phone"),
                protocol.encode_video(1, protocol.FLAG_KEYFRAME, b"\x00\x00\x00\x01\x65"),
                END_STOPPED,
            ]
        )
        recorder = Recorder()
        Session("127.0.0.1", port, "client-1", "My PC", recorder).start()
        self.assertTrue(recorder.done.wait(5))
        self.assertEqual(
            received["hello"],
            {"protocol": 1, "clientId": "client-1", "clientName": "My PC", "transports": ["udp", "tcp"]},
        )
        self.assertEqual(
            recorder.events,
            [
                ("pending",),
                ("accept", "Pixel", 1280, 720),
                ("camera", 90, "back"),
                ("frame", True, b"\x00\x00\x00\x01\x65"),
                ("end", "stopped"),
            ],
        )

    def test_unreachable_phone(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        recorder = Recorder()
        Session("127.0.0.1", port, "c", "pc", recorder).start()
        self.assertTrue(recorder.done.wait(5))
        self.assertEqual(recorder.events, [("end", CONNECT_FAILED)])

    def test_phone_closing_mid_stream_is_connection_lost(self):
        port, _ = fake_phone([protocol.encode_json(protocol.ACCEPT, {"codec": "h264"})[:-2]])
        recorder = Recorder()
        Session("127.0.0.1", port, "c", "pc", recorder).start()
        self.assertTrue(recorder.done.wait(5))
        self.assertEqual(recorder.events[-1], ("end", "connection-lost"))


class UdpSessionTest(unittest.TestCase):
    def test_video_over_udp_with_keyframe_request_after_loss(self):
        """Fake phone: accepts, waits for the computer's first FEEDBACK, then sends frames over UDP,
        dropping one datagram; the computer must ask for a keyframe and resume on it."""
        token = bytes(range(16))
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("127.0.0.1", 0))
        udp.settimeout(5)
        accept = {"codec": "h264", "udpPort": udp.getsockname()[1], "udpToken": token.hex()}
        port, received = fake_phone([protocol.encode_json(protocol.ACCEPT, accept)], hold_open=True)
        recorder = Recorder()
        session = Session("127.0.0.1", port, "c", "pc", recorder)
        session.start()

        feedback, computer = udp.recvfrom(64)
        self.assertEqual(feedback[1:17], token)

        def send(frame_id, flags=0, drop=None):
            for i, part in enumerate(protocol.encode_fragments(frame_id, frame_id, flags, bytes([frame_id]) * 3000)):
                if i != drop:
                    udp.sendto(part, computer)

        send(0, protocol.FLAG_KEYFRAME)
        send(1, drop=0)
        send(2)
        while True:  # this phone ignores NACKs, so the computer gives up on frame 1 and asks for a keyframe
            feedback, _ = udp.recvfrom(2048)
            if feedback[0] == protocol.UDP_FEEDBACK and feedback[17] & protocol.FEEDBACK_KEYFRAME:
                break
        send(3, protocol.FLAG_KEYFRAME)
        send(4)
        deadline = time.monotonic() + 5
        while sum(e[0] == "frame" for e in recorder.events) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        session.close()
        self.assertTrue(recorder.done.wait(5))
        frames = [e[2][0] for e in recorder.events if e[0] == "frame"]
        self.assertEqual(frames, [0, 3, 4])
        self.assertEqual(session.transport, "udp")
        self.assertIn("udp", received["hello"]["transports"])
        udp.close()


    def test_nack_resend_avoids_keyframe(self):
        token = bytes(16)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("127.0.0.1", 0))
        udp.settimeout(5)
        accept = {"codec": "h264", "udpPort": udp.getsockname()[1], "udpToken": token.hex()}
        port, _ = fake_phone([protocol.encode_json(protocol.ACCEPT, accept)], hold_open=True)
        recorder = Recorder()
        session = Session("127.0.0.1", port, "c", "pc", recorder)
        session.start()
        _, computer = udp.recvfrom(64)
        frames = {i: protocol.encode_fragments(i, i, protocol.FLAG_KEYFRAME if i == 0 else 0, bytes([i]) * 3000) for i in range(3)}
        for i, parts in frames.items():
            for j, part in enumerate(parts):
                if (i, j) != (1, 1):
                    udp.sendto(part, computer)
        while True:
            packet, _ = udp.recvfrom(2048)
            self.assertFalse(packet[0] == protocol.UDP_FEEDBACK and packet[17] & protocol.FEEDBACK_KEYFRAME)
            if packet[0] == protocol.UDP_NACK:
                frame_id, index = struct.unpack_from(">IH", packet, 18)
                udp.sendto(frames[frame_id][index], computer)
                break
        deadline = time.monotonic() + 5
        while sum(e[0] == "frame" for e in recorder.events) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        session.close()
        recorder.done.wait(5)
        self.assertEqual([e[2][0] for e in recorder.events if e[0] == "frame"], [0, 1, 2])
        udp.close()


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
