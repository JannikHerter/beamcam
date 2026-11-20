// A small mDNS/DNS-SD browser for finding phones, like apps/linux/src/beamcam/mdns.py. Windows has
// its own mDNS stack, but it has no API to browse for a service type that every Windows 11 build
// ships, so we send the queries ourselves. See docs/internals/overview.md#discovery.
using System.Buffers.Binary;
using System.Diagnostics;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text;

namespace Beamcam;

public sealed class DnsException(string message) : Exception(message);

public enum RecordType : ushort { A = 1, Ptr = 12, Txt = 16, Srv = 33 }

/// <summary>Data is a name (PTR), (port, target) (SRV), a dictionary (TXT) or an address string (A).</summary>
public sealed record DnsRecord(DnsName Name, RecordType Type, uint Ttl, object Data);

public sealed record Phone(string Id, string Name, string Host, int Port);

/// <summary>A DNS name as labels; compares case-insensitively like DNS does.</summary>
public sealed class DnsName : IEquatable<DnsName>
{
    public IReadOnlyList<string> Labels { get; }

    public DnsName(IEnumerable<string> labels) => Labels = labels.ToArray();

    public static DnsName Parse(string name) => new(name.Split('.', StringSplitOptions.RemoveEmptyEntries));

    public DnsName Prepend(string label) => new(Labels.Prepend(label));

    public bool Equals(DnsName? other) =>
        other is not null && Labels.Count == other.Labels.Count &&
        Labels.Zip(other.Labels).All(p => string.Equals(p.First, p.Second, StringComparison.OrdinalIgnoreCase));

    public override bool Equals(object? obj) => Equals(obj as DnsName);

    public override int GetHashCode()
    {
        var hash = new HashCode();
        foreach (var label in Labels)
            hash.Add(label, StringComparer.OrdinalIgnoreCase);
        return hash.ToHashCode();
    }

    public override string ToString() => string.Join('.', Labels);
}

public static class Mdns
{
    public static readonly IPAddress Group = IPAddress.Parse("224.0.0.251");
    public const int Port = 5353;
    // Plain (QM) questions, so phones answer by multicast. Asking for unicast answers (QU) fails on
    // Windows: its own mDNS service shares port 5353 and a unicast datagram reaches only one of the
    // sockets. Windows' firewall lets multicast answers in shortly after our question even without
    // the installer's rule.
    const ushort ClassIn = 1;

    public static readonly double[] QuerySchedule = [0.0, 1.0, 3.0]; // then every QueryInterval seconds
    public const double QueryInterval = 5.0;
    // A phone that stops answering (app closed, Wi-Fi off) disappears after this long.
    public const double StaleAfter = 3 * QueryInterval + 1.0;

    public static readonly DnsName ServiceName = DnsName.Parse(Protocol.ServiceType);

    static (DnsName Name, int Next) ReadName(ReadOnlySpan<byte> packet, int offset)
    {
        var labels = new List<string>();
        int? end = null;
        var jumps = 0;
        while (true)
        {
            if (offset >= packet.Length)
                throw new DnsException("name runs past packet");
            int length = packet[offset];
            if ((length & 0xC0) == 0xC0)
            {
                if (offset + 1 >= packet.Length)
                    throw new DnsException("truncated pointer");
                end ??= offset + 2;
                offset = ((length & 0x3F) << 8) | packet[offset + 1];
                if (++jumps > 32)
                    throw new DnsException("pointer loop");
                continue;
            }
            if ((length & 0xC0) != 0)
                throw new DnsException("bad label type");
            offset++;
            if (length == 0)
                break;
            if (offset + length > packet.Length)
                throw new DnsException("truncated label");
            labels.Add(Encoding.UTF8.GetString(packet.Slice(offset, length)));
            offset += length;
        }
        return (new DnsName(labels), end ?? offset);
    }

    static Dictionary<string, string> ParseTxt(ReadOnlySpan<byte> rdata)
    {
        var values = new Dictionary<string, string>();
        var offset = 0;
        while (offset < rdata.Length)
        {
            int length = rdata[offset];
            var take = Math.Min(length, rdata.Length - offset - 1);
            var entry = Encoding.UTF8.GetString(rdata.Slice(offset + 1, take));
            offset += 1 + length;
            if (entry.Length == 0)
                continue;
            var split = entry.IndexOf('=');
            var key = (split < 0 ? entry : entry[..split]).ToLowerInvariant();
            values.TryAdd(key, split < 0 ? "" : entry[(split + 1)..]);
        }
        return values;
    }

    /// <summary>All answer, authority and additional records of an mDNS response (queries yield none).</summary>
    public static List<DnsRecord> ParseResponse(ReadOnlySpan<byte> packet)
    {
        if (packet.Length < 12)
            throw new DnsException("short packet");
        var flags = BinaryPrimitives.ReadUInt16BigEndian(packet[2..]);
        int qdcount = BinaryPrimitives.ReadUInt16BigEndian(packet[4..]);
        var total = BinaryPrimitives.ReadUInt16BigEndian(packet[6..]) + BinaryPrimitives.ReadUInt16BigEndian(packet[8..])
            + BinaryPrimitives.ReadUInt16BigEndian(packet[10..]);
        var records = new List<DnsRecord>();
        if ((flags & 0x8000) == 0)
            return records;
        var offset = 12;
        for (var i = 0; i < qdcount; i++)
            offset = ReadName(packet, offset).Next + 4;
        for (var i = 0; i < total; i++)
        {
            (var name, offset) = ReadName(packet, offset);
            if (offset + 10 > packet.Length)
                throw new DnsException("truncated record");
            var type = BinaryPrimitives.ReadUInt16BigEndian(packet[offset..]);
            var ttl = BinaryPrimitives.ReadUInt32BigEndian(packet[(offset + 4)..]);
            int rdlength = BinaryPrimitives.ReadUInt16BigEndian(packet[(offset + 8)..]);
            offset += 10;
            var rdataStart = offset;
            offset += rdlength;
            if (offset > packet.Length)
                throw new DnsException("truncated rdata");
            var rdata = packet[rdataStart..offset];
            object? data = (RecordType)type switch
            {
                RecordType.Ptr => ReadName(packet, rdataStart).Name,
                RecordType.Srv when rdlength >= 7 => (BinaryPrimitives.ReadUInt16BigEndian(rdata[4..]), ReadName(packet, rdataStart + 6).Name),
                RecordType.Txt => ParseTxt(rdata),
                RecordType.A when rdlength == 4 => new IPAddress(rdata).ToString(),
                _ => null,
            };
            if (data is not null)
                records.Add(new DnsRecord(name, (RecordType)type, ttl, data));
        }
        return records;
    }

    internal static byte[] EncodeName(DnsName name)
    {
        var output = new List<byte>();
        foreach (var label in name.Labels)
        {
            var raw = Encoding.UTF8.GetBytes(label);
            output.Add((byte)raw.Length);
            output.AddRange(raw);
        }
        output.Add(0);
        return [.. output];
    }

    public static byte[] BuildQuery(DnsName? name = null, RecordType type = RecordType.Ptr)
    {
        var encoded = EncodeName(name ?? ServiceName);
        var packet = new byte[12 + encoded.Length + 4];
        BinaryPrimitives.WriteUInt16BigEndian(packet.AsSpan(4), 1);
        encoded.CopyTo(packet, 12);
        BinaryPrimitives.WriteUInt16BigEndian(packet.AsSpan(12 + encoded.Length), (ushort)type);
        BinaryPrimitives.WriteUInt16BigEndian(packet.AsSpan(14 + encoded.Length), ClassIn);
        return packet;
    }
}

/// <summary>Folds mDNS records into a list of phones. Pure; the browser thread feeds it.</summary>
public sealed class DiscoveryState
{
    sealed class Instance(DnsName name, string source, double seen)
    {
        public readonly DnsName Name = name;
        public string Source = source;
        public double Seen = seen;
        public int? Port;
        public DnsName? Target;
        public Dictionary<string, string> Txt = [];
    }

    readonly Dictionary<DnsName, Instance> _instances = [];
    readonly Dictionary<DnsName, string> _addresses = [];

    public void Add(IReadOnlyList<DnsRecord> records, string source, double now)
    {
        foreach (var record in records)
        {
            if (record.Type != RecordType.Ptr || !record.Name.Equals(Mdns.ServiceName))
                continue;
            var name = (DnsName)record.Data;
            if (record.Ttl == 0)
            {
                _instances.Remove(name);
                continue;
            }
            if (!_instances.TryGetValue(name, out var instance))
                instance = _instances[name] = new Instance(name, source, now);
            instance.Seen = now;
            instance.Source = source;
        }
        foreach (var record in records)
        {
            if (record.Type == RecordType.A)
                _addresses[record.Name] = (string)record.Data;
            if (!_instances.TryGetValue(record.Name, out var instance))
                continue;
            if (record.Type == RecordType.Srv)
            {
                (instance.Port, instance.Target) = ((ushort, DnsName))record.Data;
                instance.Seen = now;
            }
            else if (record.Type == RecordType.Txt)
            {
                instance.Txt = (Dictionary<string, string>)record.Data;
            }
        }
    }

    public void Expire(double now)
    {
        foreach (var key in _instances.Where(p => now - p.Value.Seen > Mdns.StaleAfter).Select(p => p.Key).ToList())
            _instances.Remove(key);
    }

    public List<DnsName> UnresolvedTargets() =>
        _instances.Values.Where(i => i.Target is not null && !_addresses.ContainsKey(i.Target)).Select(i => i.Target!).ToList();

    public List<Phone> Phones()
    {
        var phones = new List<Phone>();
        foreach (var instance in _instances.Values)
        {
            if (instance.Port is not { } port)
                continue;
            var host = instance.Target is not null && _addresses.TryGetValue(instance.Target, out var address) ? address : instance.Source;
            var label = instance.Name.Labels.Count > 0 ? instance.Name.Labels[0] : "Phone";
            var id = instance.Txt.TryGetValue("id", out var value) && value.Length > 0 ? value : label;
            phones.Add(new Phone(id, label, host, port));
        }
        return phones.OrderBy(p => p.Name.ToLowerInvariant(), StringComparer.Ordinal).ThenBy(p => p.Host, StringComparer.Ordinal).ToList();
    }
}

/// <summary>Background thread that keeps a list of phones on the local network up to date.</summary>
public sealed class MdnsBrowser(Action<List<Phone>> onChange)
{
    readonly DiscoveryState _state = new();
    readonly HashSet<IPAddress> _joined = [];
    volatile bool _stop;
    volatile bool _refresh;
    List<Phone> _last = [];

    public void Start() => new Thread(Run) { IsBackground = true, Name = "mdns" }.Start();

    public void Stop() => _stop = true;

    /// <summary>Ask again right away instead of waiting for the next scheduled query.</summary>
    public void Refresh() => _refresh = true;

    static Socket OpenSocket()
    {
        var socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
        Net.IgnoreConnectionResets(socket);
        socket.ExclusiveAddressUse = false;
        socket.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
        socket.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.MulticastTimeToLive, 255);
        socket.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.MulticastLoopback, true);
        try
        {
            socket.Bind(new IPEndPoint(IPAddress.Any, Mdns.Port));
        }
        catch (SocketException error)
        {
            // Answers to queries from other ports are sent unicast back to us, which also works.
            Log.Info($"cannot bind the mDNS port ({error.Message}); using a random port");
            socket.Bind(new IPEndPoint(IPAddress.Any, 0));
        }
        return socket;
    }

    /// <summary>IPv4 addresses of all interfaces that are up, so phones are found even when a VPN owns
    /// the default route.</summary>
    static List<IPAddress> Interfaces()
    {
        var addresses = new List<IPAddress>();
        try
        {
            foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (nic.OperationalStatus != OperationalStatus.Up || nic.NetworkInterfaceType == NetworkInterfaceType.Loopback)
                    continue;
                foreach (var unicast in nic.GetIPProperties().UnicastAddresses)
                    if (unicast.Address.AddressFamily == AddressFamily.InterNetwork && !IPAddress.IsLoopback(unicast.Address))
                        addresses.Add(unicast.Address);
            }
        }
        catch (NetworkInformationException error)
        {
            Log.Info($"cannot list network interfaces: {error.Message}");
        }
        return addresses;
    }

    List<IPAddress> JoinGroups(Socket socket)
    {
        var interfaces = Interfaces();
        foreach (var address in interfaces)
        {
            if (!_joined.Add(address))
                continue;
            try
            {
                socket.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.AddMembership, new MulticastOption(Mdns.Group, address));
            }
            catch (SocketException error)
            {
                Log.Debug($"join on {address} failed: {error.Message}");
            }
        }
        return interfaces;
    }

    static void Send(Socket socket, byte[] packet, List<IPAddress> interfaces)
    {
        var target = new IPEndPoint(Mdns.Group, Mdns.Port);
        foreach (var address in interfaces.Count > 0 ? interfaces : [IPAddress.Any])
        {
            try
            {
                socket.SetSocketOption(SocketOptionLevel.IP, SocketOptionName.MulticastInterface, address.GetAddressBytes());
                socket.SendTo(packet, target);
            }
            catch (SocketException error)
            {
                Log.Debug($"send on {address} failed: {error.Message}");
            }
        }
    }

    void Run()
    {
        Socket socket;
        try
        {
            socket = OpenSocket();
        }
        catch (SocketException error)
        {
            Log.Error($"mDNS unavailable: {error.Message}");
            return;
        }
        var clock = Stopwatch.StartNew();
        var schedule = new Queue<double>(Mdns.QuerySchedule);
        var nextQuery = 0.0;
        var buffer = new byte[9000];
        using (socket)
        {
            while (!_stop)
            {
                var now = clock.Elapsed.TotalSeconds;
                if (_refresh)
                {
                    _refresh = false;
                    nextQuery = now;
                }
                if (now >= nextQuery)
                {
                    var interfaces = JoinGroups(socket);
                    Send(socket, Mdns.BuildQuery(), interfaces);
                    foreach (var target in _state.UnresolvedTargets())
                        Send(socket, Mdns.BuildQuery(target, RecordType.A), interfaces);
                    if (schedule.Count > 0)
                        schedule.Dequeue();
                    nextQuery = schedule.Count > 0 ? schedule.Peek() : now + Mdns.QueryInterval;
                }
                var wait = Math.Clamp(nextQuery - now, 0.05, 0.5);
                try
                {
                    if (socket.Poll(TimeSpan.FromSeconds(wait), SelectMode.SelectRead))
                    {
                        EndPoint from = new IPEndPoint(IPAddress.Any, 0);
                        var length = socket.ReceiveFrom(buffer, ref from);
                        var source = ((IPEndPoint)from).Address.ToString();
                        try
                        {
                            _state.Add(Mdns.ParseResponse(buffer.AsSpan(0, length)), source, clock.Elapsed.TotalSeconds);
                        }
                        catch (DnsException error)
                        {
                            Log.Debug($"ignoring bad mDNS packet from {source}: {error.Message}");
                        }
                    }
                }
                catch (SocketException error)
                {
                    Log.Debug($"mDNS receive failed: {error.Message}");
                }
                _state.Expire(clock.Elapsed.TotalSeconds);
                var phones = _state.Phones();
                if (!phones.SequenceEqual(_last))
                {
                    _last = phones;
                    onChange(phones);
                }
            }
        }
    }
}
