"""Persistent settings in ~/.config/open-webcam/config.json."""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "open-webcam" / "config.json"


@dataclass
class Config:
    # Identifies this computer to phones, which remember the ids they have allowed.
    client_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    last_phone_id: str | None = None
    last_address: str | None = None

    @staticmethod
    def load() -> "Config":
        path = config_path()
        try:
            data = json.loads(path.read_text())
            known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
            config = Config(**known)
        except FileNotFoundError:
            config = Config()
            config.save()
        except (OSError, ValueError, TypeError) as error:
            log.warning("ignoring unreadable %s: %s", path, error)
            config = Config()
        return config

    def save(self) -> None:
        path = config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2))
            tmp.replace(path)
        except OSError as error:
            log.warning("could not save %s: %s", path, error)


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
