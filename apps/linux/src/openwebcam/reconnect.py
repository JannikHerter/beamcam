"""Whether and where to reconnect after a stream drops.

Phones often come back from a Wi-Fi drop with a new address (random MAC per connection), so the
phone is followed by its device id through mDNS instead of retrying the old address. Pure; the
window owns the timers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from openwebcam.client import CONNECT_FAILED, CONNECTION_LOST, FORGOTTEN
from openwebcam.mdns import Phone

ATTEMPTS = 10
DELAY = 3.0

# A failed connect is retried only while reconnecting; on a first, user-started connect the
# user should hear about it right away.
_RETRYABLE = {CONNECTION_LOST, CONNECT_FAILED}


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    name: str
    phone_id: str | None = None


class Reconnector:
    def __init__(self, attempts: int = ATTEMPTS) -> None:
        self._attempts = attempts
        self._left = 0
        self._target: Target | None = None
        self._attempted: Target | None = None  # where the current or last attempt went
        self._user = False  # the current attempt was started by the user, not automatically
        self._repaired = False

    @property
    def target(self) -> Target | None:
        return self._target

    @property
    def reconnecting(self) -> bool:
        return self._left > 0

    @property
    def pairing_allowed(self) -> bool:
        """Only a connection the user asked for may pair with a phone; automatic ones never do."""
        return self._user

    def connecting(self, target: Target, user: bool = True) -> None:
        """A new connection (clicked, or the automatic one at startup); nothing is retried until
        it has streamed once."""
        self._target = target
        self._attempted = target
        self._left = 0
        self._user = user
        self._repaired = False

    def attempting(self) -> Target | None:
        """The address for the next retry, recorded as the one being tried."""
        self._attempted = self._target
        return self._target

    def streaming(self, phone_id: str | None) -> None:
        if self._target is None:
            return
        if phone_id and not self._target.phone_id:
            self._target = replace(self._target, phone_id=phone_id)
        self._left = self._attempts
        self._user = False

    def phones(self, phones: list[Phone]) -> bool:
        """Follow the phone to its current address. True if it moved."""
        target = self._target
        if target is None or not target.phone_id:
            return False
        phone = next((p for p in phones if p.id == target.phone_id), None)
        if phone is None or (phone.host, phone.port) == (target.host, target.port):
            return False
        self._target = replace(target, host=phone.host, port=phone.port)
        return True

    def ended(self, reason: str) -> tuple[Target, float] | None:
        """(where, delay in seconds) for the next attempt, or None to stop. No delay when the phone
        has moved since the last attempt, since that attempt went to a dead address."""
        if reason == FORGOTTEN and self._user and not self._repaired and self._target is not None:
            # The phone forgot us; the user clicked Connect, so pair again right away (the phone asks).
            self._repaired = True
            return self._target, 0.0
        if self._target is None or self._left <= 0 or reason not in _RETRYABLE:
            self.cancel()
            return None
        self._left -= 1
        moved = self._attempted is not None and (self._attempted.host, self._attempted.port) != (
            self._target.host,
            self._target.port,
        )
        return self._target, 0.0 if moved else DELAY

    def cancel(self) -> None:
        self._left = 0
