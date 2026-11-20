import io
import unittest

from beamcam import protocol, secure


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

    def test_reader_splits_a_stream_fed_in_pieces(self):
        stream = protocol.encode_json(protocol.ACCEPT, {"a": 1}) + protocol.encode(protocol.VIDEO, b"xyz")
        reader = protocol.MessageReader()
        messages = []
        for i in range(len(stream)):
            messages += reader.feed(stream[i : i + 1])
        self.assertEqual([(m.type, m.payload) for m in messages], [(protocol.ACCEPT, b'{"a":1}'), (protocol.VIDEO, b"xyz")])
        with self.assertRaises(protocol.ProtocolError):
            reader.feed(bytes([protocol.VIDEO]) + (protocol.MAX_PAYLOAD + 1).to_bytes(4, "big"))

    def test_non_object_json_is_a_protocol_error(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.Message(protocol.ACCEPT, b"[1]").json()


if __name__ == "__main__":
    unittest.main()


class UdpTest(unittest.TestCase):
    def test_fragment_header_matches_android(self):
        parts = protocol.encode_fragments(7, 0x0102030405060708, protocol.FLAG_KEYFRAME, bytes(protocol.MAX_PLAINTEXT * 2))
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(len(p) + secure.OVERHEAD <= protocol.MAX_DATAGRAM for p in parts))
        # Same bytes as UdpTest in ProtocolTest.kt.
        self.assertEqual(parts[1][:18], bytes([0x01, 0, 0, 0, 7, 0, 1, 0, 3, 1, 1, 2, 3, 4, 5, 6, 7, 8]))
        fragment = protocol.parse_fragment(parts[2])
        self.assertEqual((fragment.frame_id, fragment.index, fragment.count, fragment.pts_us), (7, 2, 3, 0x0102030405060708))

    def test_feedback_matches_android(self):
        packet = protocol.encode_feedback(protocol.FEEDBACK_KEYFRAME, 256, 5)
        self.assertEqual(packet, bytes([0x81, 1, 0, 0, 1, 0, 0, 0, 0, 5]))

    def test_nack_matches_android(self):
        packet = protocol.encode_nack([(258, 1), (3, protocol.NACK_WHOLE_FRAME)])
        self.assertEqual(packet, bytes([0x82, 2, 0, 0, 1, 2, 0, 1, 0, 0, 0, 3, 0xFF, 0xFF]))

    def test_bad_fragments_are_ignored(self):
        good = protocol.encode_fragments(1, 0, 0, b"x")[0]
        self.assertIsNone(protocol.parse_fragment(good[:10]))
        self.assertIsNone(protocol.parse_fragment(b"\x02" + good[1:]))
        self.assertIsNone(protocol.parse_fragment(good[:5] + b"\x00\x05\x00\x05" + good[9:]))  # index >= count
