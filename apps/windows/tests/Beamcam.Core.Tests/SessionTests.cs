using System.Buffers.Binary;
using System.Net;
using System.Net.Sockets;
using System.Text.Json.Nodes;

namespace Beamcam.Tests;

// Mirrors apps/linux/tests/test_client.py.
public class SessionTests
{
    static readonly byte[] SecretBytes = Enumerable.Range(0, 32).Select(i => (byte)i).ToArray();
    static readonly TimeSpan Wait = TimeSpan.FromSeconds(10);

    static (Session, Recorder) RunSession(int port, IPairingStore pairings, bool allowPairing = false, bool wait = true)
    {
        var recorder = new Recorder();
        var session = new Session("127.0.0.1", port, "My PC", recorder, pairings, allowPairing);
        session.Start();
        if (wait)
            Assert.True(recorder.Done.Wait(Wait), "session did not end");
        return (session, recorder);
    }

    static Dictionary<string, byte[]> Known() => new() { [FakePhone.Fingerprint] = SecretBytes };

    [Fact]
    public void FirstConnectionPairsAndStreams()
    {
        var phone = new FakePhone([
            Protocol.EncodeJson(Protocol.Pending, []),
            FakePhone.Accept(b => b["secret"] = Convert.ToHexStringLower(SecretBytes)),
            Protocol.EncodeJson(Protocol.Camera, new JsonObject { ["rotation"] = 90, ["facing"] = "back" }),
            Protocol.Encode(0x7F, "from a newer phone"u8),
            Protocol.EncodeVideo(1, Protocol.FlagKeyframe, [0, 0, 0, 1, 0x65]),
            FakePhone.EndStopped,
        ]);
        var pairings = new MemoryPairings();
        var (session, recorder) = RunSession(phone.Port, pairings, allowPairing: true);
        Assert.False(phone.Hello!.ContainsKey("secret"));
        Assert.Equal(2, (int)phone.Hello["protocol"]!);
        Assert.Equal("My PC", (string)phone.Hello["clientName"]!);
        var events = recorder.Snapshot();
        Assert.Equal(["pending", Secure.PairingCode(FakePhone.Fingerprint)], events[0]);
        Assert.Equal(["accept", "Pixel", 1280, 720], events[1]);
        Assert.Equal(["camera", 90, "back"], events[2]);
        Assert.Equal("frame", events[3][0]);
        Assert.Equal(new byte[] { 0, 0, 0, 1, 0x65 }, events[3][2]);
        Assert.Equal(["end", "stopped"], events[4]);
        Assert.Equal(5, events.Count);
        Assert.Equal(SecretBytes, pairings.Secrets[FakePhone.Fingerprint]);
        Assert.Equal([(FakePhone.Fingerprint, "phone-1", "Pixel")], pairings.Paired);
        Assert.Equal(FakePhone.Fingerprint, session.Fingerprint);
    }

    [Fact]
    public void PairedPhoneGetsTheSecretAndNoPrompt()
    {
        var phone = new FakePhone([FakePhone.Accept(), FakePhone.EndStopped]);
        var pairings = new MemoryPairings(Known());
        var (_, recorder) = RunSession(phone.Port, pairings);
        Assert.Equal(Convert.ToHexStringLower(SecretBytes), (string)phone.Hello!["secret"]!);
        Assert.Equal(["end", "stopped"], recorder.Snapshot()[^1]);
        Assert.Equal([(FakePhone.Fingerprint, "Pixel")], pairings.Connections);
    }

    [Fact]
    public void UnknownPhoneIsToldNothingWithoutAClick()
    {
        // A stranger advertising our phone's name must not even learn this computer's name.
        var phone = new FakePhone([FakePhone.Accept(b => b["secret"] = Convert.ToHexStringLower(SecretBytes))]);
        var (_, recorder) = RunSession(phone.Port, new MemoryPairings(new() { ["another-phone"] = SecretBytes }));
        Assert.Equal([["end", EndReasons.UnknownPhone]], recorder.Snapshot());
        Thread.Sleep(100);
        Assert.Null(phone.Hello);
    }

    [Fact]
    public void PhoneThatForgotUsDropsThePairing()
    {
        var phone = new FakePhone([Protocol.EncodeJson(Protocol.End, new JsonObject { ["reason"] = EndReasons.Forgotten })]);
        var pairings = new MemoryPairings(Known());
        var (_, recorder) = RunSession(phone.Port, pairings);
        Assert.Equal([["end", EndReasons.Forgotten]], recorder.Snapshot());
        Assert.Empty(pairings.Secrets);
    }

    [Fact]
    public void AcceptWithoutSecretCannotPair()
    {
        var phone = new FakePhone([FakePhone.Accept()]);
        var pairings = new MemoryPairings();
        var (_, recorder) = RunSession(phone.Port, pairings, allowPairing: true);
        Assert.Equal(["end", "protocol"], recorder.Snapshot()[^1]);
        Assert.Empty(pairings.Secrets);
    }

    [Fact]
    public void SetCameraFromAnotherThread()
    {
        var phone = new FakePhone([FakePhone.Accept()], holdOpen: true);
        var (session, recorder) = RunSession(phone.Port, new MemoryPairings(Known()), wait: false);
        Assert.True(SpinWait.SpinUntil(() => recorder.Snapshot().Any(e => (string)e[0] == "accept"), Wait));
        session.SetCamera("back");
        Assert.True(SpinWait.SpinUntil(() => { lock (phone.Later) return phone.Later.Count > 0; }, Wait));
        session.Close();
        Assert.True(recorder.Done.Wait(Wait));
        Assert.Equal(Protocol.SetCamera, phone.Later[0].Type);
        Assert.Equal("back", (string)phone.Later[0].Json()["facing"]!);
        Assert.Equal(["end", "closed"], recorder.Snapshot()[^1]);
    }

    [Fact]
    public void UnreachablePhone()
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        var (_, recorder) = RunSession(port, new MemoryPairings());
        Assert.Equal([["end", EndReasons.ConnectFailed]], recorder.Snapshot());
    }

    [Fact]
    public void PhoneClosingMidStreamIsConnectionLost()
    {
        var phone = new FakePhone([FakePhone.Accept()[..^2]]);
        var (_, recorder) = RunSession(phone.Port, new MemoryPairings(Known()));
        Assert.Equal(["end", EndReasons.ConnectionLost], recorder.Snapshot()[^1]);
    }

    [Fact]
    public void CloseWhileWaitingForApproval()
    {
        var phone = new FakePhone([Protocol.EncodeJson(Protocol.Pending, [])], holdOpen: true);
        var (session, recorder) = RunSession(phone.Port, new MemoryPairings(), allowPairing: true, wait: false);
        Assert.True(SpinWait.SpinUntil(() => recorder.Snapshot().Count > 0, Wait));
        session.Close();
        Assert.True(recorder.Done.Wait(Wait));
        Assert.Equal(["end", EndReasons.Closed], recorder.Snapshot()[^1]);
    }

    [Theory]
    [InlineData("192.168.1.5", "192.168.1.5", Protocol.DefaultPort)]
    [InlineData(" 192.168.1.5:5000 ", "192.168.1.5", 5000)]
    [InlineData("[fe80::1]:5000", "fe80::1", 5000)]
    [InlineData("fe80::1", "fe80::1", Protocol.DefaultPort)]
    [InlineData("pixel.local", "pixel.local", Protocol.DefaultPort)]
    public void AddressForms(string text, string host, int port) => Assert.Equal((host, port), Address.Parse(text));

    [Theory]
    [InlineData("")]
    [InlineData(":80")]
    [InlineData("1.2.3.4:99999")]
    [InlineData("1.2.3.4:abc")]
    public void BadAddresses(string text) => Assert.Throws<FormatException>(() => Address.Parse(text));
}

/// <summary>Fake phone: accepts, waits for the computer's first sealed FEEDBACK, then streams sealed video.</summary>
public sealed class UdpSessionTests : IDisposable
{
    static readonly byte[] SecretBytes = Enumerable.Range(0, 32).Select(i => (byte)i).ToArray();
    static readonly TimeSpan Wait = TimeSpan.FromSeconds(10);
    readonly Socket _udp = new(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);

    public void Dispose() => _udp.Dispose();

    (Session, Recorder, FakePhone) Start(byte[] key)
    {
        _udp.Bind(new IPEndPoint(IPAddress.Loopback, 0));
        _udp.ReceiveTimeout = 5000;
        var port = ((IPEndPoint)_udp.LocalEndPoint!).Port;
        var phone = new FakePhone([FakePhone.Accept(b => { b["udpPort"] = port; b["udpKey"] = Convert.ToHexStringLower(key); })], holdOpen: true);
        var recorder = new Recorder();
        var session = new Session("127.0.0.1", phone.Port, "pc", recorder, new MemoryPairings(new() { [FakePhone.Fingerprint] = SecretBytes }));
        session.Start();
        return (session, recorder, phone);
    }

    /// <summary>Next datagram from the computer, opened with the phone's key.</summary>
    (byte[] Plaintext, EndPoint Computer) Receive(Sealer phone)
    {
        var buffer = new byte[2048];
        while (true)
        {
            EndPoint from = new IPEndPoint(IPAddress.Any, 0);
            var length = _udp.ReceiveFrom(buffer, ref from);
            if (phone.Open(buffer.AsSpan(0, length)) is { } plaintext)
                return (plaintext, from);
        }
    }

    static List<byte> Frames(Recorder recorder) => recorder.Snapshot().Where(e => (string)e[0] == "frame").Select(e => ((byte[])e[2])[0]).ToList();

    [Fact]
    public void SealedVideoWithKeyframeRequestAfterLoss()
    {
        var key = Enumerable.Range(32, 32).Select(i => (byte)i).ToArray();
        using var phone = new Sealer(key, Sealer.PhoneToComputer, Sealer.ComputerToPhone);
        var (session, recorder, fake) = Start(key);
        var (feedback, computer) = Receive(phone);
        Assert.Equal(Protocol.UdpFeedback, feedback[0]);

        void Send(int frameId, byte flags = 0, int? drop = null)
        {
            var parts = Protocol.EncodeFragments(frameId, frameId, flags, Enumerable.Repeat((byte)frameId, 3000).ToArray());
            for (var i = 0; i < parts.Count; i++)
                if (i != drop)
                    _udp.SendTo(phone.Seal(parts[i]), computer);
        }

        // Garbage and datagrams sealed with another key are ignored.
        _udp.SendTo(Enumerable.Repeat((byte)1, 100).ToArray(), computer);
        using (var stranger = new Sealer(new byte[32], Sealer.PhoneToComputer, Sealer.ComputerToPhone))
            _udp.SendTo(stranger.Seal(Protocol.EncodeFragments(9, 9, Protocol.FlagKeyframe, "x"u8)[0]), computer);

        Send(0, Protocol.FlagKeyframe);
        Send(1, drop: 0);
        Send(2);
        while (true) // this phone ignores NACKs, so the computer gives up on frame 1 and asks for a keyframe
        {
            var (packet, _) = Receive(phone);
            if (packet[0] == Protocol.UdpFeedback && (packet[1] & Protocol.FeedbackKeyframe) != 0)
                break;
        }
        Send(3, Protocol.FlagKeyframe);
        Send(4);
        SpinWait.SpinUntil(() => Frames(recorder).Count >= 3, Wait);
        session.Close();
        Assert.True(recorder.Done.Wait(Wait));
        Assert.Equal([0, 3, 4], Frames(recorder));
        Assert.Equal("udp", session.Transport);
        Assert.Contains("udp", fake.Hello!["transports"]!.AsArray().Select(t => (string)t!));
    }

    [Fact]
    public void NackResendAvoidsKeyframe()
    {
        var key = Enumerable.Range(64, 32).Select(i => (byte)i).ToArray();
        using var phone = new Sealer(key, Sealer.PhoneToComputer, Sealer.ComputerToPhone);
        var (session, recorder, _) = Start(key);
        var (_, computer) = Receive(phone);
        var frames = Enumerable.Range(0, 3).ToDictionary(i => i,
            i => Protocol.EncodeFragments(i, i, i == 0 ? Protocol.FlagKeyframe : (byte)0, Enumerable.Repeat((byte)i, 3000).ToArray()));
        foreach (var (i, parts) in frames)
            for (var j = 0; j < parts.Count; j++)
                if ((i, j) != (1, 1))
                    _udp.SendTo(phone.Seal(parts[j]), computer);
        while (true)
        {
            var (packet, _) = Receive(phone);
            Assert.False(packet[0] == Protocol.UdpFeedback && (packet[1] & Protocol.FeedbackKeyframe) != 0);
            if (packet[0] == Protocol.UdpNack)
            {
                var frameId = (int)BinaryPrimitives.ReadUInt32BigEndian(packet.AsSpan(2));
                int index = BinaryPrimitives.ReadUInt16BigEndian(packet.AsSpan(6));
                _udp.SendTo(phone.Seal(frames[frameId][index]), computer);
                break;
            }
        }
        SpinWait.SpinUntil(() => Frames(recorder).Count >= 3, Wait);
        session.Close();
        recorder.Done.Wait(Wait);
        Assert.Equal([0, 1, 2], Frames(recorder));
    }

    [Fact]
    public void TcpOnlySessionDoesNotOfferUdp()
    {
        var fake = new FakePhone([FakePhone.Accept(), FakePhone.EndStopped]);
        var recorder = new Recorder();
        new Session("127.0.0.1", fake.Port, "pc", recorder, new MemoryPairings(new() { [FakePhone.Fingerprint] = SecretBytes }), transports: ["tcp"]).Start();
        Assert.True(recorder.Done.Wait(Wait));
        Assert.Equal(["tcp"], fake.Hello!["transports"]!.AsArray().Select(t => (string)t!));
    }
}
