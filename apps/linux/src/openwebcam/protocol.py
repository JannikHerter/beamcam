"""Wire format shared with the Android app. Spec: docs/internals/protocol.md"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import BinaryIO

VERSION = 2
DEFAULT_PORT = 47800
SERVICE_TYPE = "_openwebcam._tcp.local."
MAX_PAYLOAD = 8 * 1024 * 1024

HELLO = 0x01
ACCEPT = 0x02
END = 0x03
VIDEO = 0x04
CAMERA = 0x05
PENDING = 0x06
SET_CAMERA = 0x10

FLAG_KEYFRAME = 0x01
FLAG_CONFIG = 0x02

_HEADER = struct.Struct(">BI")
_VIDEO_HEADER = struct.Struct(">QB")


class ProtocolError(Exception):
    pass


@dataclass(frozen=True)
class Message:
    type: int
    payload: bytes

    def json(self) -> dict:
        try:
            value = json.loads(self.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtocolError(f"bad JSON in message {self.type:#x}") from error
        if not isinstance(value, dict):
            raise ProtocolError(f"message {self.type:#x} is not a JSON object")
        return value


@dataclass(frozen=True)
class VideoFrame:
    pts_us: int
    flags: int
    data: bytes

    @property
    def keyframe(self) -> bool:
        return bool(self.flags & FLAG_KEYFRAME)


def encode(message_type: int, payload: bytes) -> bytes:
    return _HEADER.pack(message_type, len(payload)) + payload


def encode_json(message_type: int, value: dict) -> bytes:
    return encode(message_type, json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream: BinaryIO) -> Message:
    message_type, length = _HEADER.unpack(_read_exact(stream, _HEADER.size))
    if length > MAX_PAYLOAD:
        raise ProtocolError(f"message too large ({length} bytes)")
    return Message(message_type, _read_exact(stream, length))


class MessageReader:
    """Splits a byte stream into messages as data arrives, so the caller never blocks mid-message."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Message]:
        self._buffer += data
        messages = []
        while len(self._buffer) >= _HEADER.size:
            message_type, length = _HEADER.unpack_from(self._buffer)
            if length > MAX_PAYLOAD:
                raise ProtocolError(f"message too large ({length} bytes)")
            end = _HEADER.size + length
            if len(self._buffer) < end:
                break
            messages.append(Message(message_type, bytes(self._buffer[_HEADER.size : end])))
            del self._buffer[:end]
        return messages


def decode_video(payload: bytes) -> VideoFrame:
    if len(payload) < _VIDEO_HEADER.size:
        raise ProtocolError("short video message")
    pts_us, flags = _VIDEO_HEADER.unpack_from(payload)
    return VideoFrame(pts_us, flags, payload[_VIDEO_HEADER.size :])


def encode_video(pts_us: int, flags: int, data: bytes) -> bytes:
    return encode(VIDEO, _VIDEO_HEADER.pack(pts_us, flags) + data)


# UDP video (optional, negotiated in HELLO/ACCEPT); see docs/internals/protocol.md#udp-video.
# These are the plaintexts inside datagrams sealed by secure.Sealer.
UDP_VIDEO = 0x01
UDP_FEEDBACK = 0x81
UDP_NACK = 0x82
NACK_WHOLE_FRAME = 0xFFFF
MAX_NACK_ENTRIES = 200
MAX_DATAGRAM = 1400
MAX_PLAINTEXT = MAX_DATAGRAM - 24  # secure.OVERHEAD
FEEDBACK_KEYFRAME = 0x01
_FRAGMENT = struct.Struct(">BIHHBQ")
_FEEDBACK = struct.Struct(">BBII")


@dataclass(frozen=True)
class Fragment:
    frame_id: int
    index: int
    count: int
    flags: int
    pts_us: int
    data: bytes


def parse_fragment(datagram: bytes) -> Fragment | None:
    if len(datagram) < _FRAGMENT.size or datagram[0] != UDP_VIDEO:
        return None
    _type, frame_id, index, count, flags, pts_us = _FRAGMENT.unpack_from(datagram)
    if count == 0 or index >= count:
        return None
    return Fragment(frame_id, index, count, flags, pts_us, datagram[_FRAGMENT.size :])


def encode_fragments(frame_id: int, pts_us: int, flags: int, data: bytes, max_plaintext: int = MAX_PLAINTEXT) -> list[bytes]:
    chunk = max_plaintext - _FRAGMENT.size
    count = max(1, -(-len(data) // chunk))
    return [
        _FRAGMENT.pack(UDP_VIDEO, frame_id & 0xFFFFFFFF, i, count, flags, pts_us) + data[i * chunk : (i + 1) * chunk]
        for i in range(count)
    ]


def encode_feedback(flags: int, last_frame: int, lost: int) -> bytes:
    return _FEEDBACK.pack(UDP_FEEDBACK, flags, last_frame & 0xFFFFFFFF, lost & 0xFFFFFFFF)


def encode_nack(missing: list[tuple[int, int]]) -> bytes:
    """u8 type | u8 count | count x (u32 frame id, u16 index); index 0xFFFF = whole frame."""
    entries = missing[:MAX_NACK_ENTRIES]
    return struct.pack(">BB", UDP_NACK, len(entries)) + b"".join(
        struct.pack(">IH", frame_id & 0xFFFFFFFF, index) for frame_id, index in entries
    )
