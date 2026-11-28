"""Finding and creating the v4l2loopback device other apps see as a webcam."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from beamcam.i18n import N_

LABEL = "Beamcam"
SYSFS = Path("/sys/class/video4linux")
INSTALLED_HELPER = Path("/usr/lib/beamcam/setup-virtual-camera")

SECURE_BOOT_BLOCKED = N_("Secure Boot blocked the driver. Allow its key once, then try again.")
# What data/setup-virtual-camera prints on failure, so the window can show it translated.
HELPER_MESSAGES = (
    N_("The virtual camera is still in use. Close apps using it and try again."),
    N_("v4l2loopback is not installed."),
    N_("v4l2loopback is already used by another app."),
    N_("The virtual camera did not appear."),
    SECURE_BOOT_BLOCKED,
)
# Steps the window offers to open next to a helper message.
HELPER_GUIDES = {
    SECURE_BOOT_BLOCKED: "https://github.com/JannikHerter/beamcam/blob/main/docs/user/troubleshooting.md"
    "#secure-boot-blocks-the-virtual-camera",
}


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
    return run_as_root([*helper_command(), *(["--remove"] if remove else [])])


NOT_ALLOWED = N_("Your computer didn’t let Beamcam make this change. Try again, and enter your password when asked.")


def run_as_root(command: list[str]) -> tuple[bool, str]:
    """Runs a helper through pkexec. Returns (ok, error text); no text if the user cancelled."""
    if shutil.which("pkexec") is None:
        return False, "pkexec is not installed"
    command = ["pkexec", *command]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if result.returncode == 126:  # pkexec: the user dismissed the password prompt
        return False, ""
    if result.returncode == 127:  # pkexec: not authorized, e.g. a wrong password or no prompt at all
        return False, NOT_ALLOWED
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
    if "fedora" in ids:
        # The driver is in RPM Fusion, built on the computer by akmods. Creating the signing key
        # before the first build lets it load with Secure Boot once the key is enrolled.
        install = "sudo dnf install akmods && sudo kmodgenca -a && sudo dnf install akmod-v4l2loopback"
        if Path("/etc/yum.repos.d/rpmfusion-free.repo").exists():
            return install
        # Atomic desktops have no dnf install, and RHEL-likes have no %fedora for the RPM Fusion URL.
        if release.get("ID") == "fedora" and not Path("/run/ostree-booted").exists():
            return (
                "sudo dnf install https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm"
                f" && {install}"
            )
        return None
    if ids & {"opensuse", "suse", "opensuse-tumbleweed"}:
        return "sudo zypper install v4l2loopback-kmp-default"
    return None
