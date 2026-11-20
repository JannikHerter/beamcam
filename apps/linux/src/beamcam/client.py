"""One TCP session with a phone: handshake, then a stream of video and orientation messages."""

from __future__ import annotations

import logging
import select
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Protocol as Interface

from beamcam import protocol, secure
from beamcam.reassembly import Reassembler

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

    def on_pending(self, code: str) -> None: ...
    def on_accept(self, info: StreamInfo) -> None: ...
    def on_frame(self, frame: protocol.VideoFrame) -> None: ...
    def on_camera(self, rotation: int, facing: str) -> None: ...
    def on_end(self, reason: str) -> None: ...


# Reasons reported through on_end in addition to the phone's own END reasons.
CONNECT_FAILED = "connect-failed"
CONNECTION_LOST = "connection-lost"
CLOSED = "closed"
VIDEO_ERROR = "video-error"
# The phone's certificate is not one we paired with, and pairing was not asked for.
UNKNOWN_PHONE = "unknown-phone"
# END from the phone: it does not know our secret any more (it forgot this computer).
FORGOTTEN = "unknown"

END_MESSAGES = {
    "denied": "The phone did not allow this computer.",
    "timeout": "Nobody tapped Allow on the phone in time.",
    "busy": "The phone is already streaming to another computer.",
    "stopped": "Streaming was stopped on the phone.",
    "camera": "The phone’s camera stopped working.",
    "replaced": "Another Beamcam on this computer took over the phone.",
    "protocol": "The phone app and this app don’t fit together. Update both to the latest version.",
    CONNECT_FAILED: "Couldn’t reach the phone. Is Beamcam open on it, and on the same Wi-Fi?",
    CONNECTION_LOST: "Lost the connection to the phone.",
    VIDEO_ERROR: "The video from the phone couldn’t be shown.",
    FORGOTTEN: "Your phone doesn’t know this computer any more. Click Connect to pair again.",
    UNKNOWN_PHONE: "This phone isn’t paired with this computer. Click Connect to pair it.",
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


class PairingStore(Interface):
    """Where the computer keeps phone fingerprints and secrets (config.Config)."""

    def secret_for(self, fingerprint: str) -> bytes | None: ...
    def pair(self, fingerprint: str, secret: bytes, phone_id: str, name: str) -> None: ...
    def forget(self, fingerprint: str) -> None: ...


def _tls_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Phones are self-signed; trust comes from the fingerprint pinned at pairing, checked below.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _shutdown(sock: socket.socket | None) -> None:
    # socket.socket.shutdown, not SSLSocket's: that one tears down TLS state another thread is using.
    if sock is not None:
        try:
            socket.socket.shutdown(sock, socket.SHUT_RDWR)
        except OSError:
            pass


class Session:
    """One connection to a phone. All TLS reads and writes happen on the session thread, because an
    SSL socket must not be used by two threads at once; other threads only queue messages."""

    def __init__(
        self,
        host: str,
        port: int,
        client_name: str,
        listener: SessionListener,
        pairings: PairingStore,
        allow_pairing: bool = False,
        transports: tuple[str, ...] = TRANSPORTS,
    ) -> None:
        self.host = host
        self.port = port
        self.transports = transports
        #: "udp" once UDP video arrives, else "tcp".
        self.transport = "tcp"
        #: The phone's certificate fingerprint, once the TLS handshake is done.
        self.fingerprint: str | None = None
        self._pairings = pairings
        self._allow_pairing = allow_pairing
        self._udp: socket.socket | None = None
        self._udp_started = False
        self._last_video = time.monotonic()
        self._client_name = client_name
        self._listener = listener
        self._sock: socket.socket | None = None
        self._closed = threading.Event()
        self._outbox: list[bytes] = []
        self._outbox_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=f"session-{host}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._closed.set()
        _shutdown(self._sock)

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def set_camera(self, facing: str) -> None:
        with self._outbox_lock:
            self._outbox.append(protocol.encode_json(protocol.SET_CAMERA, {"facing": facing}))

    def _flush(self, sock: socket.socket) -> None:
        with self._outbox_lock:
            pending, self._outbox = self._outbox, []
        for data in pending:
            sock.sendall(data)

    def _run(self) -> None:
        reason = CLOSED
        try:
            reason = self._session()
        except (OSError, EOFError, ValueError) as error:
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
            raw = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        except OSError as error:
            log.info("cannot connect to %s:%s: %s", self.host, self.port, error)
            return CONNECT_FAILED
        self._sock = raw
        if self._closed.is_set():
            return CLOSED
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        try:
            sock = _tls_context().wrap_socket(raw)
        except (ssl.SSLError, OSError) as error:
            log.info("TLS with %s failed: %s", self.host, error)
            return CLOSED if self._closed.is_set() else "protocol"
        self._sock = sock
        fingerprint = secure.fingerprint(sock.getpeercert(binary_form=True))
        self.fingerprint = fingerprint
        secret = self._pairings.secret_for(fingerprint)
        if secret is None and not self._allow_pairing:
            # Not a phone we paired with (or a stranger using its name): send nothing at all.
            log.info("%s is not a paired phone (%s)", self.host, fingerprint[:16])
            return UNKNOWN_PHONE

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
        hello = {"protocol": protocol.VERSION, "clientName": self._client_name, "transports": offered}
        if secret is not None:
            hello["secret"] = secret.hex()
        sock.sendall(protocol.encode_json(protocol.HELLO, hello))

        reader = protocol.MessageReader()
        timeout = APPROVAL_TIMEOUT
        last_data = time.monotonic()
        while True:
            self._flush(sock)
            if not sock.pending():
                readable, _, _ = select.select([sock], [], [], 0.05)
                if not readable:
                    if timeout and time.monotonic() - last_data > timeout:
                        raise TimeoutError(f"nothing from the phone for {timeout:.0f} s")
                    continue
            data = sock.recv(256 * 1024)
            if not data:
                raise EOFError("connection closed")
            last_data = time.monotonic()
            for message in reader.feed(data):
                end = self._handle(message, sock, fingerprint, secret)
                if end is not None:
                    return end
                if message.type == protocol.ACCEPT:
                    # With UDP video the TLS connection can be quiet; the UDP loop watches liveness.
                    timeout = 0 if self._udp_started else STREAM_TIMEOUT

    def _handle(self, message: protocol.Message, sock: socket.socket, fingerprint: str, secret: bytes | None) -> str | None:
        if message.type == protocol.PENDING:
            self._listener.on_pending(secure.pairing_code(fingerprint))
        elif message.type == protocol.ACCEPT:
            body = message.json()
            if body.get("codec") != "h264":
                raise protocol.ProtocolError(f"unsupported codec {body.get('codec')!r}")
            info = StreamInfo(
                device_id=str(body.get("deviceId", "")),
                device_name=str(body.get("deviceName", "Phone")),
                width=int(body.get("width", 1280)),
                height=int(body.get("height", 720)),
                fps=int(body.get("fps", 30)),
                facing=str(body.get("facing", "front")),
            )
            new_secret = body.get("secret")
            if secret is None:
                if not isinstance(new_secret, str) or len(new_secret) != 64:
                    raise protocol.ProtocolError("phone accepted without pairing")
                self._pairings.pair(fingerprint, bytes.fromhex(new_secret), info.device_id, info.device_name)
            self._listener.on_accept(info)
            self._last_video = time.monotonic()
            try:
                key = bytes.fromhex(str(body.get("udpKey", "")))
            except ValueError:
                key = b""
            if self._udp is not None and "udpPort" in body and len(key) == 32:
                self._udp_started = True
                threading.Thread(
                    target=self._udp_loop, args=(sock.getpeername()[0], int(body["udpPort"]), key), name="udp", daemon=True
                ).start()
        elif message.type == protocol.VIDEO:
            self._last_video = time.monotonic()
            self._listener.on_frame(protocol.decode_video(message.payload))
        elif message.type == protocol.CAMERA:
            body = message.json()
            rotation = int(body.get("rotation", 0))
            if rotation in (0, 90, 180, 270):
                self._listener.on_camera(rotation, str(body.get("facing", "front")))
        elif message.type == protocol.END:
            reason = str(message.json().get("reason", "stopped"))
            if reason == FORGOTTEN:
                # The phone no longer knows our secret; pairing again needs the user's click.
                self._pairings.forget(fingerprint)
            return reason
        # Unknown types are skipped so newer phones can add messages.
        return None

    def _udp_loop(self, peer: str, port: int, key: bytes) -> None:
        """Receive UDP video, send FEEDBACK (heartbeat and keyframe requests), and watch liveness
        for both transports: if the phone fell back to TCP, its video still updates _last_video."""
        udp = self._udp
        assert udp is not None
        sealer = secure.Sealer.for_computer(key)
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
                    feedback = protocol.encode_feedback(flags, reassembler.last_frame, reassembler.losses)
                    udp.sendto(sealer.seal(feedback), (peer, port))
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
                plaintext = sealer.open(data) if source == peer else None
                fragment = protocol.parse_fragment(plaintext) if plaintext is not None else None
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
                    udp.sendto(sealer.seal(protocol.encode_nack(missing)), (peer, port))
                except OSError as error:
                    log.debug("nack failed: %s", error)
            for frame in frames:
                self._listener.on_frame(frame)
            if time.monotonic() - self._last_video > STREAM_TIMEOUT:
                log.info("no video from %s for %.0f s", peer, STREAM_TIMEOUT)
                _shutdown(self._sock)
                return
