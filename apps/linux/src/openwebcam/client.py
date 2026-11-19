"""One TCP session with a phone: handshake, then a stream of video and orientation messages."""

from __future__ import annotations

import logging
import socket
import threading
from dataclasses import dataclass
from typing import Protocol as Interface

from openwebcam import protocol

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
# The phone shows an approval prompt for up to 60 s.
APPROVAL_TIMEOUT = 75.0
# Wi-Fi drops often leave a half-open socket, so silence this long means the phone is gone.
STREAM_TIMEOUT = 8.0


@dataclass(frozen=True)
class StreamInfo:
    device_id: str
    device_name: str
    width: int
    height: int
    fps: int
    facing: str


class SessionListener(Interface):
    """Called from the session thread."""

    def on_pending(self) -> None: ...
    def on_accept(self, info: StreamInfo) -> None: ...
    def on_frame(self, frame: protocol.VideoFrame) -> None: ...
    def on_camera(self, rotation: int, facing: str) -> None: ...
    def on_end(self, reason: str) -> None: ...


# Reasons reported through on_end in addition to the phone's own END reasons.
CONNECT_FAILED = "connect-failed"
CONNECTION_LOST = "connection-lost"
CLOSED = "closed"
VIDEO_ERROR = "video-error"

END_MESSAGES = {
    "denied": "The phone did not allow this computer.",
    "timeout": "Nobody tapped Allow on the phone in time.",
    "busy": "The phone is already streaming to another computer.",
    "stopped": "Streaming was stopped on the phone.",
    "camera": "The phone’s camera stopped working.",
    "protocol": "The phone app and this app don’t fit together. Update both to the latest version.",
    CONNECT_FAILED: "Couldn’t reach the phone. Is Open Webcam open on it, and on the same Wi-Fi?",
    CONNECTION_LOST: "Lost the connection to the phone.",
    VIDEO_ERROR: "The video from the phone couldn’t be shown.",
}


def end_message(reason: str) -> str:
    return END_MESSAGES.get(reason, f"Streaming stopped ({reason}).")


def parse_address(text: str) -> tuple[str, int]:
    """'192.168.1.5', '192.168.1.5:47800' or '[fe80::1]:47800' -> (host, port)."""
    text = text.strip()
    if not text:
        raise ValueError("empty address")
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = rest.lstrip(":")
    elif text.count(":") == 1:
        host, port = text.split(":")
    else:
        host, port = text, ""
    if not host:
        raise ValueError("missing host")
    if not port:
        return host, protocol.DEFAULT_PORT
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"bad port {port!r}")
    return host, int(port)


class Session:
    def __init__(self, host: str, port: int, client_id: str, client_name: str, listener: SessionListener) -> None:
        self.host = host
        self.port = port
        self._client_id = client_id
        self._client_name = client_name
        self._listener = listener
        self._sock: socket.socket | None = None
        self._closed = threading.Event()
        self._send_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=f"session-{host}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._closed.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def set_camera(self, facing: str) -> None:
        self._send(protocol.encode_json(protocol.SET_CAMERA, {"facing": facing}))

    def _send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            return
        with self._send_lock:
            try:
                sock.sendall(data)
            except OSError as error:
                log.debug("send failed: %s", error)

    def _run(self) -> None:
        reason = CLOSED
        try:
            reason = self._session()
        except (OSError, EOFError) as error:
            log.info("session with %s ended: %s", self.host, error)
            reason = CLOSED if self._closed.is_set() else CONNECTION_LOST
        except protocol.ProtocolError as error:
            log.warning("protocol error from %s: %s", self.host, error)
            reason = "protocol"
        finally:
            if self._sock is not None:
                self._sock.close()
        self._listener.on_end(CLOSED if self._closed.is_set() else reason)

    def _session(self) -> str:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        except OSError as error:
            log.info("cannot connect to %s:%s: %s", self.host, self.port, error)
            return CONNECT_FAILED
        self._sock = sock
        if self._closed.is_set():
            return CLOSED
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        self._send(
            protocol.encode_json(
                protocol.HELLO,
                {"protocol": protocol.VERSION, "clientId": self._client_id, "clientName": self._client_name},
            )
        )
        sock.settimeout(APPROVAL_TIMEOUT)
        stream = sock.makefile("rb", buffering=256 * 1024)
        while True:
            message = protocol.read_message(stream)
            if message.type == protocol.PENDING:
                self._listener.on_pending()
            elif message.type == protocol.ACCEPT:
                body = message.json()
                if body.get("codec") != "h264":
                    raise protocol.ProtocolError(f"unsupported codec {body.get('codec')!r}")
                self._listener.on_accept(
                    StreamInfo(
                        device_id=str(body.get("deviceId", "")),
                        device_name=str(body.get("deviceName", "Phone")),
                        width=int(body.get("width", 1280)),
                        height=int(body.get("height", 720)),
                        fps=int(body.get("fps", 30)),
                        facing=str(body.get("facing", "front")),
                    )
                )
                sock.settimeout(STREAM_TIMEOUT)
            elif message.type == protocol.VIDEO:
                self._listener.on_frame(protocol.decode_video(message.payload))
            elif message.type == protocol.CAMERA:
                body = message.json()
                rotation = int(body.get("rotation", 0))
                if rotation in (0, 90, 180, 270):
                    self._listener.on_camera(rotation, str(body.get("facing", "front")))
            elif message.type == protocol.END:
                return str(message.json().get("reason", "stopped"))
            # Unknown types are skipped so newer phones can add messages.
