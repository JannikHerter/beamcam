"""One TCP session with a phone: handshake, then a stream of video and orientation messages."""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Protocol as Interface

from openwebcam import protocol
from openwebcam.reassembly import Reassembler

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
# The phone shows an approval prompt for up to 60 s.
APPROVAL_TIMEOUT = 75.0
# Wi-Fi drops often leave a half-open socket, so silence this long means the phone is gone.
STREAM_TIMEOUT = 8.0
# UDP: the first FEEDBACK opens the path through the computer's firewall; the phone waits 1 s for it.
PUNCH_INTERVAL = 0.1
PUNCH_PHASE = 2.0
FEEDBACK_INTERVAL = 1.0
KEYFRAME_RETRY = 0.25
TRANSPORTS = ("udp", "tcp")


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
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        client_name: str,
        listener: SessionListener,
        transports: tuple[str, ...] = TRANSPORTS,
    ) -> None:
        self.host = host
        self.port = port
        self.transports = transports
        #: "udp" once UDP video arrives, else "tcp".
        self.transport = "tcp"
        self._udp: socket.socket | None = None
        self._last_video = time.monotonic()
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
            if self._udp is not None:
                self._udp.close()
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
        offered = ["tcp"]
        if "udp" in self.transports and sock.family == socket.AF_INET:
            try:
                udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                # Keyframes arrive as a burst of ~70 datagrams; don't drop them in the kernel.
                udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
                udp.bind((sock.getsockname()[0], 0))
                self._udp = udp
                offered.insert(0, "udp")
            except OSError as error:
                log.info("UDP unavailable: %s", error)
        self._send(
            protocol.encode_json(
                protocol.HELLO,
                {
                    "protocol": protocol.VERSION,
                    "clientId": self._client_id,
                    "clientName": self._client_name,
                    "transports": offered,
                },
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
                self._last_video = time.monotonic()
                if self._udp is not None and "udpPort" in body:
                    token = bytes.fromhex(str(body.get("udpToken", "")))
                    peer = sock.getpeername()[0]
                    # Video may now come over UDP, so TCP silence alone no longer means the phone is gone.
                    sock.settimeout(None)
                    threading.Thread(
                        target=self._udp_loop, args=(peer, int(body["udpPort"]), token), name="udp", daemon=True
                    ).start()
                else:
                    sock.settimeout(STREAM_TIMEOUT)
            elif message.type == protocol.VIDEO:
                self._last_video = time.monotonic()
                self._listener.on_frame(protocol.decode_video(message.payload))
            elif message.type == protocol.CAMERA:
                body = message.json()
                rotation = int(body.get("rotation", 0))
                if rotation in (0, 90, 180, 270):
                    self._listener.on_camera(rotation, str(body.get("facing", "front")))
            elif message.type == protocol.END:
                return str(message.json().get("reason", "stopped"))
            # Unknown types are skipped so newer phones can add messages.

    def _udp_loop(self, peer: str, port: int, token: bytes) -> None:
        """Receive UDP video, send FEEDBACK (heartbeat and keyframe requests), and watch liveness
        for both transports: if the phone fell back to TCP, its video still updates _last_video."""
        udp = self._udp
        assert udp is not None
        udp.settimeout(0.02)
        reassembler = Reassembler()
        started = time.monotonic()
        next_feedback = started
        last_request = 0.0
        receiving = False
        first_packet = 0.0
        while not self._closed.is_set():
            now = time.monotonic()
            # Ask only after a loss, or if the phone's first keyframe never completes.
            want_keyframe = (
                receiving
                and reassembler.waiting_for_keyframe
                and (reassembler.losses > 0 or now - first_packet > 1.0)
                and now - last_request > KEYFRAME_RETRY
            )
            if (receiving or now - started < PUNCH_PHASE) and (now >= next_feedback or want_keyframe):
                if want_keyframe:
                    last_request = now
                flags = protocol.FEEDBACK_KEYFRAME if want_keyframe else 0
                try:
                    udp.sendto(protocol.encode_feedback(token, flags, reassembler.last_frame, reassembler.losses), (peer, port))
                except OSError as error:
                    log.debug("feedback failed: %s", error)
                next_feedback = now + (FEEDBACK_INTERVAL if receiving else PUNCH_INTERVAL)
            try:
                data, (source, _port) = udp.recvfrom(2048)
            except TimeoutError:
                frames = reassembler.poll(time.monotonic())
            except OSError:
                return  # socket closed by _run
            else:
                fragment = protocol.parse_fragment(data) if source == peer else None
                if fragment is None:
                    continue
                if not receiving:
                    receiving = True
                    first_packet = time.monotonic()
                    self.transport = "udp"
                    log.info("receiving video over UDP")
                now = time.monotonic()
                self._last_video = now
                frames = reassembler.add(fragment, now)
            missing = reassembler.missing(time.monotonic())
            if missing:
                try:
                    udp.sendto(protocol.encode_nack(token, missing), (peer, port))
                except OSError as error:
                    log.debug("nack failed: %s", error)
            for frame in frames:
                self._listener.on_frame(frame)
            if time.monotonic() - self._last_video > STREAM_TIMEOUT:
                log.info("no video from %s for %.0f s", peer, STREAM_TIMEOUT)
                sock = self._sock
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                return
