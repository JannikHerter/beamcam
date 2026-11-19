import io
import unittest

from openwebcam import protocol


class ProtocolTest(unittest.TestCase):
    def test_header_matches_android(self):
        # Same bytes as ProtocolTest.kt, so both sides agree on the framing.
        self.assertEqual(protocol.encode(protocol.VIDEO, bytes([7, 8, 9])), bytes([0x04, 0, 0, 0, 3, 7, 8, 9]))

    def test_video_payload_matches_android(self):
        message = protocol.read_message(
            io.BytesIO(protocol.encode_video(0x0102030405060708, protocol.FLAG_KEYFRAME, bytes([0, 0, 0, 1, 0x65])))
        )
        self.assertEqual(message.payload, bytes([1, 2, 3, 4, 5, 6, 7, 8, 1, 0, 0, 0, 1, 0x65]))
        frame = protocol.decode_video(message.payload)
        self.assertEqual(frame.pts_us, 0x0102030405060708)
        self.assertTrue(frame.keyframe)
        self.assertEqual(frame.data, bytes([0, 0, 0, 1, 0x65]))

    def test_json_round_trip(self):
        stream = io.BytesIO(protocol.encode_json(protocol.ACCEPT, {"width": 1280}) + protocol.encode(protocol.PENDING, b"{}"))
        self.assertEqual(protocol.read_message(stream).json(), {"width": 1280})
        self.assertEqual(protocol.read_message(stream).type, protocol.PENDING)

    def test_oversized_message_is_rejected(self):
        header = bytes([protocol.VIDEO]) + (protocol.MAX_PAYLOAD + 1).to_bytes(4, "big")
        with self.assertRaises(protocol.ProtocolError):
            protocol.read_message(io.BytesIO(header))

    def test_truncated_stream_is_eof(self):
        with self.assertRaises(EOFError):
            protocol.read_message(io.BytesIO(bytes([protocol.VIDEO, 0, 0, 0, 9, 1, 2])))

    def test_non_object_json_is_a_protocol_error(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.Message(protocol.ACCEPT, b"[1]").json()


if __name__ == "__main__":
    unittest.main()
