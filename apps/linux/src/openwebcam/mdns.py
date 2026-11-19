"""A small mDNS/DNS-SD browser for finding phones.

Avahi is not running on every distro (it is off by default on Arch and Manjaro), so we speak
just enough multicast DNS ourselves: send PTR queries for our service type and read the
PTR/SRV/TXT/A answers. See docs/internals/overview.md#discovery.
"""

from __future__ import annotations

import fcntl
import logging
import select
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from openwebcam import protocol

log = logging.getLogger(__name__)

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33
CLASS_IN = 1

QUERY_SCHEDULE = (0.0, 1.0, 3.0)  # then every QUERY_INTERVAL seconds
QUERY_INTERVAL = 5.0
# A phone that stops answering (app closed, Wi-Fi off) disappears after this long.
STALE_AFTER = 3 * QUERY_INTERVAL + 1.0


class DnsError(Exception):
    pass


@dataclass(frozen=True)
class Record:
    name: tuple[str, ...]
    type: int
    ttl: int
    data: object  # PTR: name tuple, SRV: (port, target), TXT: dict, A: str


@dataclass(frozen=True)
class Phone:
    id: str
    name: str
    host: str
    port: int


def name_key(name: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(label.lower() for label in name)


def split_name(name: str) -> tuple[str, ...]:
    return tuple(label for label in name.split(".") if label)


SERVICE_NAME = split_name(protocol.SERVICE_TYPE)


def _read_name(packet: bytes, offset: int) -> tuple[tuple[str, ...], int]:
    labels: list[str] = []
    end = None
    jumps = 0
    while True:
        if offset >= len(packet):
            raise DnsError("name runs past packet")
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise DnsError("truncated pointer")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | packet[offset + 1]
            jumps += 1
            if jumps > 32:
                raise DnsError("pointer loop")
            continue
        if length & 0xC0:
            raise DnsError("bad label type")
        offset += 1
        if length == 0:
            break
        label = packet[offset : offset + length]
        if len(label) != length:
            raise DnsError("truncated label")
        labels.append(label.decode("utf-8", errors="replace"))
        offset += length
    return tuple(labels), (end if end is not None else offset)


def _parse_txt(rdata: bytes) -> dict[str, str]:
    values: dict[str, str] = {}
    offset = 0
    while offset < len(rdata):
        length = rdata[offset]
        entry = rdata[offset + 1 : offset + 1 + length].decode("utf-8", errors="replace")
        offset += 1 + length
        if not entry:
            continue
        key, _, value = entry.partition("=")
        values.setdefault(key.lower(), value)
    return values


def parse_response(packet: bytes) -> list[Record]:
    """All answer, authority and additional records of an mDNS response (queries yield [])."""
    if len(packet) < 12:
        raise DnsError("short packet")
    _id, flags, qdcount, ancount, nscount, arcount = struct.unpack_from(">HHHHHH", packet)
    if not flags & 0x8000:
        return []
    offset = 12
    for _ in range(qdcount):
        _name, offset = _read_name(packet, offset)
        offset += 4
    records: list[Record] = []
    for _ in range(ancount + nscount + arcount):
        name, offset = _read_name(packet, offset)
        if offset + 10 > len(packet):
            raise DnsError("truncated record")
        rtype, _rclass, ttl, rdlength = struct.unpack_from(">HHIH", packet, offset)
        offset += 10
        rdata_start = offset
        offset += rdlength
        if offset > len(packet):
            raise DnsError("truncated rdata")
        rdata = packet[rdata_start:offset]
        if rtype == TYPE_PTR:
            data: object = _read_name(packet, rdata_start)[0]
        elif rtype == TYPE_SRV and rdlength >= 7:
            _prio, _weight, port = struct.unpack_from(">HHH", packet, rdata_start)
            data = (port, _read_name(packet, rdata_start + 6)[0])
        elif rtype == TYPE_TXT:
            data = _parse_txt(rdata)
        elif rtype == TYPE_A and rdlength == 4:
            data = socket.inet_ntoa(rdata)
        else:
            continue
        records.append(Record(name, rtype, ttl, data))
    return records


def _encode_name(name: tuple[str, ...]) -> bytes:
    out = b""
    for label in name:
        raw = label.encode("utf-8")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def build_query(name: tuple[str, ...] = SERVICE_NAME, qtype: int = TYPE_PTR) -> bytes:
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + _encode_name(name) + struct.pack(">HH", qtype, CLASS_IN)


@dataclass
class _Instance:
    name: tuple[str, ...]
    source: str
    seen: float
    port: int | None = None
    target: tuple[str, ...] | None = None
    txt: dict[str, str] = field(default_factory=dict)


class DiscoveryState:
    """Folds mDNS records into a list of phones. Pure; the browser thread feeds it."""

    def __init__(self) -> None:
        self._instances: dict[tuple[str, ...], _Instance] = {}
        self._addresses: dict[tuple[str, ...], str] = {}

    def add(self, records: list[Record], source: str, now: float) -> None:
        service = name_key(SERVICE_NAME)
        for record in records:
            if record.type == TYPE_PTR and name_key(record.name) == service:
                key = name_key(record.data)  # type: ignore[arg-type]
                if record.ttl == 0:
                    self._instances.pop(key, None)
                    continue
                instance = self._instances.get(key)
                if instance is None:
                    instance = self._instances[key] = _Instance(record.data, source, now)  # type: ignore[arg-type]
                instance.seen = now
                instance.source = source
        for record in records:
            key = name_key(record.name)
            if record.type == TYPE_A:
                self._addresses[key] = record.data  # type: ignore[assignment]
            instance = self._instances.get(key)
            if instance is None:
                continue
            if record.type == TYPE_SRV:
                instance.port, instance.target = record.data  # type: ignore[misc]
                instance.seen = now
            elif record.type == TYPE_TXT:
                instance.txt = record.data  # type: ignore[assignment]

    def expire(self, now: float) -> None:
        for key in [k for k, v in self._instances.items() if now - v.seen > STALE_AFTER]:
            del self._instances[key]

    def unresolved_targets(self) -> list[tuple[str, ...]]:
        return [
            i.target
            for i in self._instances.values()
            if i.target is not None and name_key(i.target) not in self._addresses
        ]

    def phones(self) -> list[Phone]:
        phones = []
        for instance in self._instances.values():
            if instance.port is None:
                continue
            host = self._addresses.get(name_key(instance.target or ()), instance.source)
            label = instance.name[0] if instance.name else "Phone"
            phones.append(Phone(id=instance.txt.get("id") or label, name=label, host=host, port=instance.port))
        return sorted(phones, key=lambda p: (p.name.lower(), p.host))


def _ipv4_interfaces() -> list[str]:
    """IPv4 addresses of all interfaces, so phones are found even when a VPN owns the default route."""
    addresses = []
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _index, name in socket.if_nameindex():
            try:
                packed = fcntl.ioctl(probe.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))  # SIOCGIFADDR
            except OSError:
                continue
            address = socket.inet_ntoa(packed[20:24])
            if not address.startswith("127."):
                addresses.append(address)
    finally:
        probe.close()
    return addresses


class Browser:
    """Background thread that keeps a list of phones on the local network up to date."""

    def __init__(self, on_change: Callable[[list[Phone]], None]) -> None:
        self._on_change = on_change
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = DiscoveryState()
        self._joined: set[str] = set()
        self._last: list[Phone] = []
        self._refresh_requested = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mdns", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def refresh(self) -> None:
        """Ask again right away instead of waiting for the next scheduled query."""
        self._refresh_requested = True

    def _open_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        try:
            sock.bind(("", MDNS_PORT))
        except OSError as error:
            # Answers to queries from other ports are sent unicast back to us, which also works.
            log.info("cannot bind mDNS port (%s); using a random port", error)
            sock.bind(("", 0))
        return sock

    def _join_groups(self, sock: socket.socket) -> list[str]:
        interfaces = _ipv4_interfaces()
        for address in interfaces:
            if address in self._joined:
                continue
            mreq = socket.inet_aton(MDNS_GROUP) + socket.inet_aton(address)
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError as error:
                log.debug("join on %s failed: %s", address, error)
            self._joined.add(address)
        return interfaces

    def _send(self, sock: socket.socket, packet: bytes, interfaces: list[str]) -> None:
        for address in interfaces or ["0.0.0.0"]:
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
                sock.sendto(packet, (MDNS_GROUP, MDNS_PORT))
            except OSError as error:
                log.debug("send on %s failed: %s", address, error)

    def _run(self) -> None:
        try:
            sock = self._open_socket()
        except OSError:
            log.exception("mDNS unavailable")
            return
        started = time.monotonic()
        schedule = list(QUERY_SCHEDULE)
        next_query = started
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if self._refresh_requested:
                    self._refresh_requested = False
                    next_query = now
                if now >= next_query:
                    interfaces = self._join_groups(sock)
                    self._send(sock, build_query(), interfaces)
                    for target in self._state.unresolved_targets():
                        self._send(sock, build_query(target, TYPE_A), interfaces)
                    if schedule:
                        schedule.pop(0)
                    next_query = started + schedule[0] if schedule else now + QUERY_INTERVAL
                readable, _, _ = select.select([sock], [], [], max(0.05, min(0.5, next_query - now)))
                if readable:
                    packet, (source, _port) = sock.recvfrom(9000)
                    try:
                        self._state.add(parse_response(packet), source, time.monotonic())
                    except DnsError as error:
                        log.debug("ignoring bad mDNS packet from %s: %s", source, error)
                self._state.expire(time.monotonic())
                phones = self._state.phones()
                if phones != self._last:
                    self._last = phones
                    self._on_change(phones)
        finally:
            sock.close()
