import unittest

from openwebcam import protocol
from openwebcam.reassembly import LOSS_TIMEOUT, MAX_RECOVER_FRAMES, NACK_RETRY, Reassembler

KEY = protocol.FLAG_KEYFRAME


def fragments(frame_id, flags=0, size=3000):
    data = bytes([frame_id % 256]) * size
    return [protocol.parse_fragment(d) for d in protocol.encode_fragments(frame_id, frame_id * 33_000, flags, data)]


def feed(reassembler, frags, now=0.0):
    out = []
    for f in frags:
        out += reassembler.add(f, now)
    return [protocol_frame.pts_us // 33_000 for protocol_frame in out]


class ReassemblerTest(unittest.TestCase):
    def test_in_order_frames(self):
        r = Reassembler()
        self.assertEqual(feed(r, fragments(0, KEY) + fragments(1) + fragments(2)), [0, 1, 2])
        self.assertFalse(r.waiting_for_keyframe)

    def test_waits_for_first_keyframe(self):
        r = Reassembler()
        self.assertEqual(feed(r, fragments(5)), [])
        self.assertTrue(r.waiting_for_keyframe)
        self.assertEqual(feed(r, fragments(6, KEY) + fragments(7)), [6, 7])

    def test_reordered_fragments_and_frames(self):
        r = Reassembler()
        a, b = fragments(0, KEY), fragments(1)
        self.assertEqual(feed(r, [b[1], a[2], b[0], a[0], b[2], a[1]]), [0, 1])

    def test_lost_fragment_skips_to_next_keyframe(self):
        r = Reassembler()
        feed(r, fragments(0, KEY))
        damaged = fragments(1)[1:]  # first piece of frame 1 never arrives
        self.assertEqual(feed(r, damaged + fragments(2) + fragments(3), now=1.0), [])
        # Newer frames waited past the reorder window: frame 1 is lost, P-frames 2-3 are held back.
        self.assertEqual(r.poll(1.0 + LOSS_TIMEOUT * 2), [])
        self.assertTrue(r.waiting_for_keyframe)
        self.assertEqual(r.losses, 1)
        self.assertEqual(feed(r, fragments(4) + fragments(5, KEY) + fragments(6), now=2.0), [5, 6])

    def test_late_fragment_within_window_is_not_a_loss(self):
        r = Reassembler()
        feed(r, fragments(0, KEY))
        f1 = fragments(1)
        self.assertEqual(feed(r, f1[1:] + fragments(2), now=1.0), [])
        self.assertEqual(feed(r, f1[:1], now=1.0 + LOSS_TIMEOUT / 2), [1, 2])
        self.assertEqual(r.losses, 0)

    def test_missing_pieces_are_reported_once_per_retry(self):
        r = Reassembler()
        feed(r, fragments(0, KEY))
        f1 = fragments(1)
        feed(r, [f1[0], f1[2]], now=1.0)
        self.assertEqual(r.missing(1.0), [(1, 1)])
        self.assertEqual(r.missing(1.0 + NACK_RETRY / 2), [])
        self.assertEqual(r.missing(1.0 + NACK_RETRY), [(1, 1)])

    def test_trailing_pieces_and_whole_frames_need_a_newer_packet(self):
        r = Reassembler()
        feed(r, fragments(0, KEY))
        feed(r, fragments(1)[:1], now=1.0)
        self.assertEqual(r.missing(1.0), [])  # the rest may still be on its way
        feed(r, fragments(3)[:1], now=1.01)
        self.assertEqual(r.missing(1.01), [(1, 1), (1, 2), (2, protocol.NACK_WHOLE_FRAME)])

    def test_resent_piece_recovers_without_a_keyframe(self):
        r = Reassembler()
        feed(r, fragments(0, KEY))
        f1 = fragments(1)
        self.assertEqual(feed(r, f1[1:] + fragments(2), now=1.0), [])
        self.assertEqual(r.missing(1.0), [(1, 0)])
        self.assertEqual(feed(r, f1[:1], now=1.0 + LOSS_TIMEOUT * 0.8), [1, 2])
        self.assertEqual((r.losses, r.waiting_for_keyframe), (0, False))

    def test_outage_skips_to_keyframe_instead_of_resending_stale_video(self):
        r = Reassembler()
        feed(r, fragments(0, KEY))
        after_outage = 2 + MAX_RECOVER_FRAMES
        self.assertEqual(feed(r, fragments(after_outage), now=1.0), [])
        self.assertTrue(r.waiting_for_keyframe)
        self.assertEqual(r.missing(1.0), [])  # nothing old is worth asking for

    def test_duplicates_are_harmless(self):
        r = Reassembler()
        f0 = fragments(0, KEY)
        self.assertEqual(feed(r, f0 + f0 + fragments(1)), [0, 1])


if __name__ == "__main__":
    unittest.main()
