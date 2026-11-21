import os
import stat
import tempfile
import unittest
from unittest import mock

from beamcam import secure
from beamcam.config import Config

KEY = bytes(range(32))


class SealerTest(unittest.TestCase):
    def test_phone_vector_matches_android(self):
        # Same vector as SecureTest.kt: key 00..1f, phone -> computer, counter 5.
        sealed = bytes.fromhex("0000000000000005dce30c780dc7f8121966254e4c0e7dbe5911f64374d135")
        self.assertEqual(secure.Sealer.for_computer(KEY).open(sealed), b"beamcam")

    def test_round_trip_and_rejections(self):
        computer = secure.Sealer.for_computer(KEY)
        phone = secure.Sealer(KEY, secure.PHONE_TO_COMPUTER, secure.COMPUTER_TO_PHONE)
        sealed = computer.seal(b"hello")
        self.assertEqual(phone.open(sealed), b"hello")
        self.assertIsNone(computer.open(sealed))  # own direction
        tampered = bytearray(sealed)
        tampered[9] ^= 1
        self.assertIsNone(phone.open(bytes(tampered)))
        self.assertIsNone(phone.open(sealed[:10]))
        self.assertNotEqual(computer.seal(b"hello")[:8], sealed[:8])  # counter advances

    def test_pairing_code_matches_android(self):
        import hashlib

        self.assertEqual(secure.pairing_code(hashlib.sha256(b"test").hexdigest()), "412 545")


class PairingStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.dir.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pairings_persist_privately(self):
        config = Config.load()
        config.pair("fp1", KEY, "phone-1", "Pixel")
        path = os.path.join(self.dir.name, "beamcam", "config.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        again = Config.load()
        self.assertEqual(again.secret_for("fp1"), KEY)
        self.assertTrue(again.is_paired("phone-1"))

    def test_new_key_for_same_phone_id_keeps_the_old_pairing(self):
        config = Config.load()
        config.pair("old-fp", KEY, "phone-1", "Pixel")
        config.pair("new-fp", bytes(32), "phone-1", "Pixel")
        self.assertEqual(config.secret_for("old-fp"), KEY)
        self.assertEqual(config.secret_for("new-fp"), bytes(32))

    def test_forget(self):
        config = Config.load()
        config.pair("fp1", KEY, "phone-1", "Pixel")
        config.pair("fp2", KEY, "phone-2", "Other")
        config.last_phone_id = "phone-1"
        config.forget("fp1")
        self.assertIsNone(config.secret_for("fp1"))
        self.assertIsNone(config.last_phone_id)
        config.forget_all()
        self.assertEqual(Config.load().pairings, {})


if __name__ == "__main__":
    unittest.main()
