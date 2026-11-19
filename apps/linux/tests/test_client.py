import socket
import threading
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


def fake_phone(script):
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
        self.assertEqual(received["hello"], {"protocol": 1, "clientId": "client-1", "clientName": "My PC"})
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
