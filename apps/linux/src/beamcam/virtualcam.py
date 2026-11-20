"""Finding and creating the v4l2loopback device other apps see as a webcam."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

LABEL = "Beamcam"
SYSFS = Path("/sys/class/video4linux")
INSTALLED_HELPER = Path("/usr/lib/beamcam/setup-virtual-camera")


@dataclass(frozen=True)
class VirtualCamera:
    path: str
    label: str

    @property
    def writable(self) -> bool:
        return os.access(self.path, os.W_OK)


def find_cameras(sysfs: Path = SYSFS) -> list[VirtualCamera]:
    """v4l2loopback devices, ours first. Real webcams live under a bus device, loopback ones
    under /sys/devices/virtual."""
    cameras = []
    if not sysfs.is_dir():
        return cameras
    for entry in sorted(sysfs.iterdir()):
        if "/devices/virtual/" not in str(entry.resolve()):
            continue
        try:
            label = (entry / "name").read_text().strip()
        except OSError:
            continue
        cameras.append(VirtualCamera(f"/dev/{entry.name}", label))
    cameras.sort(key=lambda c: c.label != LABEL)
    return cameras


def find_camera() -> VirtualCamera | None:
    cameras = find_cameras()
    return cameras[0] if cameras else None


def module_installed() -> bool:
    modinfo = shutil.which("modinfo") or "/usr/sbin/modinfo"
    if Path("/sys/module/v4l2loopback").exists():
        return True
    try:
        return subprocess.run([modinfo, "v4l2loopback"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def helper_command() -> list[str]:
    """How pkexec runs the root helper. pkexec needs a real file path. A distro package installs
    the helper with a polkit action, so the password prompt says what it is for; a checkout or a
    pipx install runs the bundled copy through /bin/sh and gets the generic prompt."""
    if INSTALLED_HELPER.exists():
        return [str(INSTALLED_HELPER)]
    return ["/bin/sh", str(resources.files("beamcam").joinpath("data/setup-virtual-camera"))]


def run_helper(remove: bool = False) -> tuple[bool, str]:
    """Create (or remove) the virtual camera as root via pkexec. Returns (ok, error text)."""
    if shutil.which("pkexec") is None:
        return False, "pkexec is not installed"
    command = ["pkexec", *helper_command()]
    if remove:
        command.append("--remove")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if result.returncode in (126, 127):  # pkexec: dismissed / not authorized
        return False, ""
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, ""


def _os_release() -> dict[str, str]:
    values = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            key, _, value = line.partition("=")
            values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def install_command() -> str | None:
    """Command that installs v4l2loopback on this distro, if we know it."""
    release = _os_release()
    ids = {release.get("ID", "")} | set(release.get("ID_LIKE", "").split())
    if "manjaro" in ids:
        # Manjaro names headers after the kernel series: 6.18.x-MANJARO -> linux618-headers.
        major, _, rest = os.uname().release.partition(".")
        headers = f"linux{major}{rest.split('.')[0]}-headers"
        return f"sudo pacman -S --needed v4l2loopback-dkms {headers}"
    if ids & {"arch", "endeavouros", "cachyos"}:
        return "sudo pacman -S --needed v4l2loopback-dkms linux-headers"
    if ids & {"debian", "ubuntu", "linuxmint", "pop"}:
        return "sudo apt install v4l2loopback-dkms"
    if ids & {"fedora"}:
        return "sudo dnf install akmod-v4l2loopback  # needs RPM Fusion"
    if ids & {"opensuse", "suse", "opensuse-tumbleweed"}:
        return "sudo zypper install v4l2loopback-kmp-default"
    return None
