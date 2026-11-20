"""Persistent settings in ~/.config/beamcam/config.json."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "beamcam" / "config.json"


_lock = threading.Lock()


@dataclass(frozen=True)
class PairedPhone:
    fingerprint: str
    phone_id: str
    name: str
    last_connected: int  # unix seconds, 0 if unknown (paired before this was stored)


@dataclass
class Config:
    last_phone_id: str | None = None
    last_address: str | None = None
    language: str | None = None  # None: the computer's language
    # Phones this computer paired with, by certificate fingerprint (hex):
    # {"secret": hex, "phone_id": str, "name": str, "last_connected": unix seconds}.
    # The secret is what the phone allowed.
    pairings: dict[str, dict] = field(default_factory=dict)

    @staticmethod
    def load() -> "Config":
        path = config_path()
        try:
            data = json.loads(path.read_text())
            known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
            config = Config(**known)
            if not isinstance(config.pairings, dict):
                config.pairings = {}
        except FileNotFoundError:
            config = Config()
        except (OSError, ValueError, TypeError) as error:
            log.warning("ignoring unreadable %s: %s", path, error)
            config = Config()
        return config

    def save(self) -> None:
        path = config_path()
        with _lock:
            data = json.dumps(asdict(self), indent=2)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.unlink(missing_ok=True)
                # Holds pairing secrets: readable by this user only.
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as file:
                    file.write(data)
                tmp.replace(path)
            except OSError as error:
                log.warning("could not save %s: %s", path, error)

    # Pairings. Called from session threads and the window, hence the lock.

    def secret_for(self, fingerprint: str) -> bytes | None:
        with _lock:
            entry = self.pairings.get(fingerprint)
        try:
            return bytes.fromhex(entry["secret"]) if entry else None
        except (KeyError, ValueError, TypeError):
            return None

    def pair(self, fingerprint: str, secret: bytes, phone_id: str, name: str) -> None:
        with _lock:
            # A phone that got a new key (app reinstalled) replaces its old pairing.
            for old in [fp for fp, e in self.pairings.items() if phone_id and e.get("phone_id") == phone_id]:
                del self.pairings[old]
            self.pairings[fingerprint] = {
                "secret": secret.hex(), "phone_id": phone_id, "name": name, "last_connected": int(time.time())
            }
        self.save()

    def connected(self, fingerprint: str, name: str) -> None:
        """A paired phone accepted again; it may have been renamed."""
        with _lock:
            entry = self.pairings.get(fingerprint)
            if entry is None:
                return
            entry["name"] = name
            entry["last_connected"] = int(time.time())
        self.save()

    def paired_phones(self) -> list[PairedPhone]:
        """Most recently connected first."""
        with _lock:
            phones = [
                PairedPhone(fp, str(e.get("phone_id", "")), str(e.get("name") or "Phone"), int(e.get("last_connected") or 0))
                for fp, e in self.pairings.items()
            ]
        return sorted(phones, key=lambda p: p.last_connected, reverse=True)

    def forget(self, fingerprint: str) -> None:
        with _lock:
            entry = self.pairings.pop(fingerprint, None)
            if entry and entry.get("phone_id") == self.last_phone_id:
                self.last_phone_id = None
        self.save()

    def forget_all(self) -> None:
        with _lock:
            self.pairings.clear()
            self.last_phone_id = None
        self.save()

    def is_paired(self, phone_id: str) -> bool:
        with _lock:
            return any(e.get("phone_id") == phone_id for e in self.pairings.values())


def computer_name() -> str:
    """The name shown on the phone when this computer asks to connect."""
    try:
        for line in Path("/etc/machine-info").read_text().splitlines():
            if line.startswith("PRETTY_HOSTNAME="):
                value = line.split("=", 1)[1].strip().strip('"')
                if value:
                    return value
    except OSError:
        pass
    return socket.gethostname() or "Linux computer"
