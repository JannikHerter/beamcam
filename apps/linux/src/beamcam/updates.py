"""New versions through the computer's own updates: once the user said yes, the root helper adds
Beamcam's package repository (docs/internals/updates.md). Only the .deb and .rpm install the
helper; the AUR, pip and a checkout leave updates to whoever installed them."""

from __future__ import annotations

import configparser
from pathlib import Path

from beamcam import virtualcam
from beamcam.i18n import N_

HELPER = Path("/usr/lib/beamcam/updates")
# What the helper writes; any of them, unless disabled, means updates are on.
SOURCES = (Path("/etc/apt/sources.list.d/beamcam.sources"), Path("/etc/yum.repos.d/beamcam.repo"))
# GNOME Software and dnf config-manager turn a dnf repository off in here instead of its file.
DNF_OVERRIDES = Path("/etc/dnf/repos.override.d")
# What data/updates prints on failure, so the window can show it translated.
HELPER_MESSAGES = (
    N_("Your computer is installing other software. Try again when it has finished."),
    N_("Beamcam’s download page can’t be reached. Check that this computer is online, then try again."),
)
OFF = ("no", "false", "0")
# An Ubuntu release upgrade disables other repositories like this; the helper writes them anew.
DISABLED = {f"enabled{sep}{value}" for sep in (":", "=") for value in OFF}


def available(helper: Path = HELPER) -> bool:
    return helper.exists()


def turned_on(sources: tuple[Path, ...] = SOURCES, dnf_overrides: Path = DNF_OVERRIDES) -> bool:
    for path in sources:
        try:
            lines = {"".join(line.lower().split()) for line in path.read_text().splitlines()}
        except (OSError, UnicodeError):
            continue
        override = _dnf_override(dnf_overrides) if path.suffix == ".repo" else None
        on = not lines & DISABLED if override is None else override
        if on:
            return True
    return False


def _dnf_override(folder: Path) -> bool | None:
    """Whether dnf's override files turn the repository on or off, if they say. dnf reads them in
    name order, and they outrank the repository's own file."""
    enabled = None
    for path in sorted(folder.glob("*.repo")):
        parser = configparser.RawConfigParser(strict=False)
        try:
            parser.read(path)
        except (OSError, UnicodeError, configparser.Error):
            continue  # unreadable for this user: dnf's default mode lets everyone read it
        if parser.has_option("beamcam", "enabled"):
            enabled = parser.get("beamcam", "enabled").strip().lower()
    return None if enabled is None else enabled not in OFF


def should_ask(available: bool, turned_on: bool, answer: bool | None) -> bool:
    """Asks until this user answered No, unless the repository is on. A yes asks again once the
    repository is gone or disabled, which a release upgrade does without telling anyone."""
    return available and not turned_on and answer is not False


def turn(on: bool) -> tuple[bool, str]:
    """Adds or removes the repository as root. Returns (ok, error text); no text if cancelled."""
    return virtualcam.run_as_root([str(HELPER), *([] if on else ["--remove"])])
