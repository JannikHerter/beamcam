import os
import stat
import tempfile
import unittest
from unittest import mock

from beamcam.config import Config, config_path


class PairingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.dir.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def test_pairings_survive_a_restart_and_stay_private(self):
        config = Config()
        config.pair("fp1", b"\x01" * 32, "phone-1", "Pixel")
        loaded = Config.load()
        self.assertEqual(loaded.secret_for("fp1"), b"\x01" * 32)
        self.assertTrue(loaded.is_paired("phone-1"))
        self.assertEqual(stat.S_IMODE(config_path().stat().st_mode), 0o600)

    def test_newest_connection_first_and_renames_are_kept(self):
        config = Config()
        with mock.patch("beamcam.config.time.time", return_value=100):
            config.pair("fp1", b"\x01" * 32, "phone-1", "Pixel")
        with mock.patch("beamcam.config.time.time", return_value=200):
            config.pair("fp2", b"\x02" * 32, "phone-2", "Galaxy")
        with mock.patch("beamcam.config.time.time", return_value=300):
            config.connected("fp1", "Work phone")
        phones = Config.load().paired_phones()
        self.assertEqual([(p.name, p.last_connected) for p in phones], [("Work phone", 300), ("Galaxy", 200)])

    def test_connected_does_not_pair_an_unknown_phone(self):
        config = Config()
        config.connected("fp-unknown", "Stranger")
        self.assertEqual(config.paired_phones(), [])

    def test_forgetting_one_phone_keeps_the_others(self):
        config = Config()
        config.pair("fp1", b"\x01" * 32, "phone-1", "Pixel")
        config.pair("fp2", b"\x02" * 32, "phone-2", "Galaxy")
        config.last_phone_id = "phone-1"
        config.forget("fp1")
        self.assertEqual([p.phone_id for p in config.paired_phones()], ["phone-2"])
        self.assertIsNone(config.last_phone_id)

    def test_a_stranger_claiming_a_phones_id_cannot_erase_its_pairing(self):
        config = Config()
        config.pair("real-key", b"\x01" * 32, "phone-1", "Pixel")
        config.last_phone_id = "phone-1"
        config.pair("other-key", b"\x02" * 32, "phone-1", "Pixel")
        self.assertEqual(config.secret_for("real-key"), b"\x01" * 32)
        config.forget("other-key")
        self.assertEqual(config.last_phone_id, "phone-1")
        self.assertEqual(Config.load().secret_for("real-key"), b"\x01" * 32)

    def test_pairings_from_before_last_connected_was_stored(self):
        config = Config(pairings={"fp1": {"secret": "01" * 32, "phone_id": "phone-1", "name": "Pixel"}})
        self.assertEqual(config.paired_phones()[0].last_connected, 0)


if __name__ == "__main__":
    unittest.main()
