import os
import tempfile
import unittest
from pathlib import Path

from beamcam import virtualcam


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


class RunAsRootTest(unittest.TestCase):
    def run_with_pkexec_exit(self, code: int) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory() as folder:
            pkexec = Path(folder, "pkexec")
            pkexec.write_text(f"#!/bin/sh\necho 'helper said this' >&2\nexit {code}\n")
            pkexec.chmod(0o755)
            path = os.environ["PATH"]
            os.environ["PATH"] = f"{folder}:{path}"
            try:
                return virtualcam.run_as_root(["/bin/true"])
            finally:
                os.environ["PATH"] = path

    def test_dismissed_is_quiet_and_refused_says_what_to_try(self):
        self.assertEqual(self.run_with_pkexec_exit(0), (True, ""))
        self.assertEqual(self.run_with_pkexec_exit(126), (False, ""))
        self.assertEqual(self.run_with_pkexec_exit(127), (False, virtualcam.NOT_ALLOWED))
        self.assertEqual(self.run_with_pkexec_exit(1), (False, "helper said this"))


if __name__ == "__main__":
    unittest.main()
