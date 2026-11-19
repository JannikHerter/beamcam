"""Wire format shared with the Android app. Spec: docs/internals/protocol.md"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import BinaryIO

VERSION = 1
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


def decode_video(payload: bytes) -> VideoFrame:
    if len(payload) < _VIDEO_HEADER.size:
        raise ProtocolError("short video message")
    pts_us, flags = _VIDEO_HEADER.unpack_from(payload)
    return VideoFrame(pts_us, flags, payload[_VIDEO_HEADER.size :])


def encode_video(pts_us: int, flags: int, data: bytes) -> bytes:
    return encode(VIDEO, _VIDEO_HEADER.pack(pts_us, flags) + data)
