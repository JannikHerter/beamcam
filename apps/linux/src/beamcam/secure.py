"""Crypto shared with the Android app (Secure.kt). Spec: docs/internals/protocol.md#identity-and-pairing"""

from __future__ import annotations

import hashlib
import struct
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PHONE_TO_COMPUTER = 1
COMPUTER_TO_PHONE = 2
TAG_BYTES = 16
OVERHEAD = 8 + TAG_BYTES
_COUNTER = struct.Struct(">Q")


def fingerprint(certificate_der: bytes) -> str:
    """What a computer pins for a phone: SHA-256 of its certificate, hex."""
    return hashlib.sha256(certificate_der).hexdigest()


def pairing_code(fingerprint_hex: str) -> str:
    """6 digits both screens show, so a user can spot a machine in the middle on first pairing."""
    value = int(fingerprint_hex[:8], 16) % 1_000_000
    digits = f"{value:06d}"
    return f"{digits[:3]} {digits[3:]}"


class Sealer:
    """AES-256-GCM for UDP datagrams: u64 counter | ciphertext | 16 B tag.
    Nonce = u32 direction | u64 counter, so each direction of a session key has its own nonce space."""

    def __init__(self, key: bytes, send_direction: int, receive_direction: int) -> None:
        if len(key) != 32:
            raise ValueError("AES-256 key needed")
        self._aead = AESGCM(key)
        self._send = send_direction
        self._receive = receive_direction
        self._counter = 0
        self._lock = threading.Lock()

    @classmethod
    def for_computer(cls, key: bytes) -> "Sealer":
        return cls(key, COMPUTER_TO_PHONE, PHONE_TO_COMPUTER)

    def seal(self, plaintext: bytes) -> bytes:
        with self._lock:
            counter = self._counter
            self._counter += 1
        nonce = struct.pack(">IQ", self._send, counter)
        return _COUNTER.pack(counter) + self._aead.encrypt(nonce, plaintext, None)

    def open(self, datagram: bytes) -> bytes | None:
        """The plaintext, or None if it was not sealed with this key in this direction."""
        if len(datagram) < OVERHEAD:
            return None
        (counter,) = _COUNTER.unpack_from(datagram)
        try:
            return self._aead.decrypt(struct.pack(">IQ", self._receive, counter), datagram[8:], None)
        except InvalidTag:
            return None
