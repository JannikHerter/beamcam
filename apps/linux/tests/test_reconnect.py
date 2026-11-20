import unittest

from beamcam.client import CLOSED, CONNECT_FAILED, CONNECTION_LOST, FORGOTTEN, UNKNOWN_PHONE
from beamcam.mdns import Phone
from beamcam.reconnect import DELAY, Reconnector, Target

PHONE = Target("192.168.2.147", 47800, "Pixel", "phone-1")


def streamed(target=PHONE, attempts=3):
    r = Reconnector(attempts)
    r.connecting(target)
    r.streaming("phone-1")
    return r


class ReconnectorTest(unittest.TestCase):
    def test_first_connect_failure_is_not_retried(self):
        r = Reconnector()
        r.connecting(PHONE)
        self.assertIsNone(r.ended(CONNECT_FAILED))

    def test_lost_stream_retries_after_delay(self):
        self.assertEqual(streamed().ended(CONNECTION_LOST), (PHONE, DELAY))

    def test_failed_reconnect_keeps_retrying_until_attempts_run_out(self):
        r = streamed(attempts=2)
        self.assertIsNotNone(r.ended(CONNECTION_LOST))
        r.attempting()
        self.assertIsNotNone(r.ended(CONNECT_FAILED))
        r.attempting()
        self.assertIsNone(r.ended(CONNECT_FAILED))
        self.assertFalse(r.reconnecting)

    def test_follows_phone_to_new_address(self):
        r = streamed()
        r.ended(CONNECTION_LOST)
        r.attempting()  # goes to the old .147 and fails
        moved = r.phones([Phone(id="phone-1", name="Pixel", host="192.168.2.148", port=47800)])
        self.assertTrue(moved)
        target, delay = r.ended(CONNECT_FAILED)
        self.assertEqual((target.host, delay), ("192.168.2.148", 0.0))

    def test_manual_connect_learns_phone_id_from_accept(self):
        r = Reconnector()
        r.connecting(Target("192.168.2.147", 47800, "192.168.2.147"))
        r.streaming("phone-1")
        self.assertTrue(r.phones([Phone(id="phone-1", name="Pixel", host="192.168.2.150", port=47800)]))
        self.assertEqual(r.target.host, "192.168.2.150")

    def test_other_phones_and_same_address_do_not_count(self):
        r = streamed()
        self.assertFalse(r.phones([Phone(id="other", name="X", host="192.168.2.9", port=47800)]))
        self.assertFalse(r.phones([Phone(id="phone-1", name="Pixel", host="192.168.2.147", port=47800)]))

    def test_user_stop_and_phone_end_reasons_are_final(self):
        for reason in (CLOSED, "stopped", "denied", "replaced", UNKNOWN_PHONE):
            with self.subTest(reason=reason):
                self.assertIsNone(streamed().ended(reason))

    def test_only_a_click_may_pair(self):
        r = Reconnector()
        r.connecting(PHONE)
        self.assertTrue(r.pairing_allowed)
        r.connecting(PHONE, user=False)  # the automatic connect at startup
        self.assertFalse(r.pairing_allowed)
        r = streamed()
        r.ended(CONNECTION_LOST)
        self.assertFalse(r.pairing_allowed)  # reconnects never pair

    def test_clicked_connect_to_a_phone_that_forgot_us_pairs_again_once(self):
        r = Reconnector()
        r.connecting(PHONE)
        self.assertEqual(r.ended(FORGOTTEN), (PHONE, 0.0))
        self.assertTrue(r.pairing_allowed)
        self.assertIsNone(r.ended(FORGOTTEN))

    def test_automatic_connect_to_a_phone_that_forgot_us_stops(self):
        r = Reconnector()
        r.connecting(PHONE, user=False)
        self.assertIsNone(r.ended(FORGOTTEN))
        self.assertIsNone(streamed().ended(FORGOTTEN))

    def test_streaming_again_resets_attempts(self):
        r = streamed(attempts=1)
        r.ended(CONNECTION_LOST)
        r.streaming("phone-1")
        self.assertIsNotNone(r.ended(CONNECTION_LOST))


if __name__ == "__main__":
    unittest.main()
