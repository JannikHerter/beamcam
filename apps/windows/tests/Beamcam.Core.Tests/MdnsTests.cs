using System.Buffers.Binary;
using System.Net;
using System.Text;

namespace Beamcam.Tests;

// Mirrors apps/linux/tests/test_mdns.py.
public class MdnsTests
{
    static readonly DnsName Service = Mdns.ServiceName;
    static readonly DnsName InstanceName = Service.Prepend("Pixel 9");
    static readonly DnsName HostName = DnsName.Parse("Android_abc.local");

    static byte[] Record(DnsName name, RecordType type, uint ttl, byte[] rdata)
    {
        var header = new byte[10];
        BinaryPrimitives.WriteUInt16BigEndian(header, (ushort)type);
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(2), 0x8001);
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(4), ttl);
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(8), (ushort)rdata.Length);
        return [.. Mdns.EncodeName(name), .. header, .. rdata];
    }

    static byte[] Txt(params (string Key, string Value)[] values) =>
        values.SelectMany(v => { var e = Encoding.UTF8.GetBytes($"{v.Key}={v.Value}"); return new[] { (byte)e.Length }.Concat(e); }).ToArray();

    static byte[] Response(params byte[][] records)
    {
        var header = new byte[12];
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(2), 0x8400);
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(6), (ushort)records.Length);
        return [.. header, .. records.SelectMany(r => r)];
    }

    static byte[] Srv(int port, DnsName target)
    {
        var fixedPart = new byte[6];
        BinaryPrimitives.WriteUInt16BigEndian(fixedPart.AsSpan(4), (ushort)port);
        return [.. fixedPart, .. Mdns.EncodeName(target)];
    }

    static byte[] Announcement(uint ttl = 120, int port = 47800, string address = "192.168.1.50") => Response(
        Record(Service, RecordType.Ptr, ttl, Mdns.EncodeName(InstanceName)),
        Record(InstanceName, RecordType.Srv, ttl, Srv(port, HostName)),
        Record(InstanceName, RecordType.Txt, ttl, Txt(("id", "abc-123"), ("v", "1"))),
        Record(HostName, RecordType.A, ttl, IPAddress.Parse(address).GetAddressBytes()));

    [Fact]
    public void FullAnnouncement()
    {
        var records = Mdns.ParseResponse(Announcement());
        Assert.Equal([RecordType.Ptr, RecordType.Srv, RecordType.Txt, RecordType.A], records.Select(r => r.Type));
        Assert.Equal(InstanceName, records[0].Data);
        Assert.Equal(((ushort)47800, HostName), ((ushort, DnsName))records[1].Data);
        Assert.Equal(new Dictionary<string, string> { ["id"] = "abc-123", ["v"] = "1" }, records[2].Data);
        Assert.Equal("192.168.1.50", records[3].Data);
    }

    [Fact]
    public void CompressedNames()
    {
        // PTR rdata points back at the question-less answer's owner name ("_beamcam._tcp.local" at offset 12).
        byte[] rdata = [3, .. "Cam"u8, 0xC0, 12];
        var record = new byte[10];
        BinaryPrimitives.WriteUInt16BigEndian(record, 12);
        BinaryPrimitives.WriteUInt16BigEndian(record.AsSpan(2), 1);
        BinaryPrimitives.WriteUInt32BigEndian(record.AsSpan(4), 120);
        BinaryPrimitives.WriteUInt16BigEndian(record.AsSpan(8), (ushort)rdata.Length);
        var header = new byte[12];
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(2), 0x8400);
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(6), 1);
        var ptr = Assert.Single(Mdns.ParseResponse([.. header, .. Mdns.EncodeName(Service), .. record, .. rdata]));
        Assert.Equal(Service.Prepend("Cam"), ptr.Data);
    }

    [Fact]
    public void PointerLoopIsRejected()
    {
        var header = new byte[12];
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(2), 0x8400);
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(6), 1);
        Assert.Throws<DnsException>(() => Mdns.ParseResponse([.. header, 0xC0, 12]));
    }

    [Fact]
    public void QueriesAreIgnored() => Assert.Empty(Mdns.ParseResponse(Mdns.BuildQuery()));

    [Fact]
    public void QueryAsksForTheServiceWithAMulticastAnswer()
    {
        var query = Mdns.BuildQuery();
        Assert.Equal(1, BinaryPrimitives.ReadUInt16BigEndian(query.AsSpan(4)));
        Assert.Equal(Mdns.EncodeName(Service), query[12..^4]);
        Assert.Equal((ushort)RecordType.Ptr, BinaryPrimitives.ReadUInt16BigEndian(query.AsSpan(query.Length - 4)));
        Assert.Equal(0x0001, BinaryPrimitives.ReadUInt16BigEndian(query.AsSpan(query.Length - 2))); // no QU bit
    }

    [Fact]
    public void TruncatedPacket() => Assert.Throws<DnsException>(() => Mdns.ParseResponse(Announcement()[..^3]));

    [Fact]
    public void NamesCompareLikeDns() => Assert.Equal(DnsName.Parse("_BeamCam._TCP.local"), Service);

    [Fact]
    public void AnnouncementYieldsPhone()
    {
        var state = new DiscoveryState();
        state.Add(Mdns.ParseResponse(Announcement()), "192.168.1.50", now: 0);
        Assert.Equal([new Phone("abc-123", "Pixel 9", "192.168.1.50", 47800)], state.Phones());
    }

    [Fact]
    public void MissingAddressFallsBackToSender()
    {
        var state = new DiscoveryState();
        state.Add(Mdns.ParseResponse(Announcement()).Where(r => r.Type != RecordType.A).ToList(), "10.0.0.7", now: 0);
        Assert.Equal("10.0.0.7", state.Phones()[0].Host);
        Assert.Equal([HostName], state.UnresolvedTargets());
    }

    [Fact]
    public void GoodbyeRemovesPhone()
    {
        var state = new DiscoveryState();
        state.Add(Mdns.ParseResponse(Announcement()), "192.168.1.50", now: 0);
        state.Add(Mdns.ParseResponse(Announcement(ttl: 0)), "192.168.1.50", now: 1);
        Assert.Empty(state.Phones());
    }

    [Fact]
    public void SilentPhoneExpires()
    {
        var state = new DiscoveryState();
        state.Add(Mdns.ParseResponse(Announcement()), "192.168.1.50", now: 0);
        state.Expire(now: Mdns.StaleAfter - 1);
        Assert.Single(state.Phones());
        state.Expire(now: Mdns.StaleAfter + 1);
        Assert.Empty(state.Phones());
    }

    [Fact]
    public void OtherServicesAreIgnored()
    {
        var other = DnsName.Parse("_airplay._tcp.local");
        var state = new DiscoveryState();
        state.Add(Mdns.ParseResponse(Response(Record(other, RecordType.Ptr, 120, Mdns.EncodeName(other.Prepend("TV"))))), "192.168.1.9", now: 0);
        Assert.Empty(state.Phones());
    }

    [Fact]
    public void PhoneThatMovedGetsItsNewAddress()
    {
        var state = new DiscoveryState();
        state.Add(Mdns.ParseResponse(Announcement(address: "192.168.1.50")), "192.168.1.50", now: 0);
        state.Add(Mdns.ParseResponse(Announcement(address: "192.168.1.51")), "192.168.1.51", now: 1);
        Assert.Equal("192.168.1.51", Assert.Single(state.Phones()).Host);
    }
}
