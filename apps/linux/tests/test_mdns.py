import socket
import struct
import unittest

from beamcam import mdns

SERVICE = mdns.SERVICE_NAME
INSTANCE = ("Pixel 9",) + SERVICE
HOST = ("Android_abc", "local")


def record(name, rtype, ttl, rdata):
    return mdns._encode_name(name) + struct.pack(">HHIH", rtype, 0x8001, ttl, len(rdata)) + rdata


def txt(**values):
    return b"".join(bytes([len(e)]) + e for e in (f"{k}={v}".encode() for k, v in values.items()))


def response(*records):
    body = b"".join(records)
    packet = struct.pack(">HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + body
    return packet


def phone_announcement(ttl=120, port=47800, address="192.168.1.50"):
    return response(
        record(SERVICE, mdns.TYPE_PTR, ttl, mdns._encode_name(INSTANCE)),
        record(INSTANCE, mdns.TYPE_SRV, ttl, struct.pack(">HHH", 0, 0, port) + mdns._encode_name(HOST)),
        record(INSTANCE, mdns.TYPE_TXT, ttl, txt(id="abc-123", v="1")),
        record(HOST, mdns.TYPE_A, ttl, socket.inet_aton(address)),
    )


class ParseTest(unittest.TestCase):
    def test_full_announcement(self):
        records = mdns.parse_response(phone_announcement())
        self.assertEqual([r.type for r in records], [mdns.TYPE_PTR, mdns.TYPE_SRV, mdns.TYPE_TXT, mdns.TYPE_A])
        self.assertEqual(records[0].data, INSTANCE)
        self.assertEqual(records[1].data, (47800, HOST))
        self.assertEqual(records[2].data, {"id": "abc-123", "v": "1"})
        self.assertEqual(records[3].data, "192.168.1.50")

    def test_compressed_names(self):
        # PTR rdata points back at the question-less answer's owner name ("_beamcam._tcp.local" at offset 12).
        owner = mdns._encode_name(SERVICE)
        rdata = bytes([len(b"Cam")]) + b"Cam" + struct.pack(">H", 0xC000 | 12)
        packet = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0) + owner + struct.pack(">HHIH", 12, 1, 120, len(rdata)) + rdata
        (ptr,) = mdns.parse_response(packet)
        self.assertEqual(ptr.data, ("Cam",) + SERVICE)

    def test_pointer_loop_is_rejected(self):
        packet = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0) + struct.pack(">H", 0xC000 | 12)
        with self.assertRaises(mdns.DnsError):
            mdns.parse_response(packet)

    def test_queries_are_ignored(self):
        self.assertEqual(mdns.parse_response(mdns.build_query()), [])

    def test_truncated_packet(self):
        with self.assertRaises(mdns.DnsError):
            mdns.parse_response(phone_announcement()[:-3])


class StateTest(unittest.TestCase):
    def test_announcement_yields_phone(self):
        state = mdns.DiscoveryState()
        state.add(mdns.parse_response(phone_announcement()), "192.168.1.50", now=0)
        self.assertEqual(state.phones(), [mdns.Phone(id="abc-123", name="Pixel 9", host="192.168.1.50", port=47800)])

    def test_missing_address_falls_back_to_sender(self):
        state = mdns.DiscoveryState()
        records = [r for r in mdns.parse_response(phone_announcement()) if r.type != mdns.TYPE_A]
        state.add(records, "10.0.0.7", now=0)
        self.assertEqual(state.phones()[0].host, "10.0.0.7")
        self.assertEqual(state.unresolved_targets(), [HOST])

    def test_goodbye_removes_phone(self):
        state = mdns.DiscoveryState()
        state.add(mdns.parse_response(phone_announcement()), "192.168.1.50", now=0)
        state.add(mdns.parse_response(phone_announcement(ttl=0)), "192.168.1.50", now=1)
        self.assertEqual(state.phones(), [])

    def test_silent_phone_expires(self):
        state = mdns.DiscoveryState()
        state.add(mdns.parse_response(phone_announcement()), "192.168.1.50", now=0)
        state.expire(now=mdns.STALE_AFTER - 1)
        self.assertEqual(len(state.phones()), 1)
        state.expire(now=mdns.STALE_AFTER + 1)
        self.assertEqual(state.phones(), [])

    def test_other_services_are_ignored(self):
        other = ("_airplay", "_tcp", "local")
        packet = response(record(other, mdns.TYPE_PTR, 120, mdns._encode_name(("TV",) + other)))
        state = mdns.DiscoveryState()
        state.add(mdns.parse_response(packet), "192.168.1.9", now=0)
        self.assertEqual(state.phones(), [])


if __name__ == "__main__":
    unittest.main()
