import os
import tempfile
import unittest
from pathlib import Path

from openwebcam import virtualcam


class FindCamerasTest(unittest.TestCase):
    def test_only_loopback_devices_ours_first(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            devices = {
                "video0": ("devices/pci0000:00/usb1/1-1/video4linux/video0", "Integrated Camera"),
                "video4": ("devices/virtual/video4linux/video4", "OBS Virtual Camera"),
                "video5": ("devices/virtual/video4linux/video5", virtualcam.LABEL),
            }
            sysfs = root / "class" / "video4linux"
            sysfs.mkdir(parents=True)
            for name, (path, label) in devices.items():
                target = root / path
                target.mkdir(parents=True)
                (target / "name").write_text(label + "\n")
                os.symlink(target, sysfs / name)
            cameras = virtualcam.find_cameras(sysfs)
        self.assertEqual(
            cameras,
            [virtualcam.VirtualCamera("/dev/video5", virtualcam.LABEL), virtualcam.VirtualCamera("/dev/video4", "OBS Virtual Camera")],
        )

    def test_no_sysfs(self):
        self.assertEqual(virtualcam.find_cameras(Path("/nonexistent")), [])


if __name__ == "__main__":
    unittest.main()
