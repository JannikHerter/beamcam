// One TCP session with a phone: handshake, then a stream of video and orientation messages.
// Same behavior as apps/linux/src/beamcam/client.py.
using System.Diagnostics;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json.Nodes;

namespace Beamcam;

public sealed record StreamInfo(string DeviceId, string DeviceName, int Width, int Height, int Fps, string Facing);

/// <summary>Called from the session's threads.</summary>
public interface ISessionListener
{
    void OnPending(string code);
    void OnAccept(StreamInfo info);
    void OnFrame(VideoFrame frame);
    void OnCamera(int rotation, string facing);
    void OnEnd(string reason);
}

/// <summary>Where the computer keeps phone fingerprints and secrets (Config).</summary>
public interface IPairingStore
{
    byte[]? SecretFor(string fingerprint);
    void Pair(string fingerprint, byte[] secret, string phoneId, string name);
    void Connected(string fingerprint, string name);
    void Forget(string fingerprint);
}

/// <summary>Reasons reported through OnEnd in addition to the phone's own END reasons.</summary>
public static class EndReasons
{
    public const string ConnectFailed = "connect-failed";
    public const string ConnectionLost = "connection-lost";
    public const string Closed = "closed";
    public const string VideoError = "video-error";
    /// <summary>The phone's certificate is not one we paired with, and pairing was not asked for.</summary>
    public const string UnknownPhone = "unknown-phone";
    /// <summary>END from the phone: it does not know our secret any more (it forgot this computer).</summary>
    public const string Forgotten = "unknown";
    public const string Protocol = "protocol";

    static readonly Dictionary<string, string> Messages = new()
    {
        ["denied"] = "The phone did not allow this computer.",
        ["timeout"] = "Nobody tapped Allow on the phone in time.",
        ["busy"] = "The phone is being used by another computer.",
        ["stopped"] = "Streaming was stopped on the phone.",
        ["camera"] = "The phone’s camera stopped working.",
        ["replaced"] = "Another Beamcam on this computer took over the phone.",
        [Protocol] = "The phone app and this app don’t fit together. Update both to the latest version.",
        [ConnectFailed] = "Couldn’t reach the phone. Is Beamcam open on it, and on the same Wi-Fi?",
        [ConnectionLost] = "Lost the connection to the phone.",
        [VideoError] = "The video from the phone couldn’t be shown.",
        [Forgotten] = "Your phone doesn’t know this computer any more. Click Connect to pair again.",
        [UnknownPhone] = "This phone isn’t paired with this computer. Click Connect to pair it.",
    };

    public static string Message(string reason) =>
        Messages.TryGetValue(reason, out var text) ? text : $"Streaming stopped ({reason}).";
}

public static class Address
{
    /// <summary>"192.168.1.5", "192.168.1.5:47800" or "[fe80::1]:47800" -> (host, port).</summary>
    public static (string Host, int Port) Parse(string text)
    {
        text = text.Trim();
        if (text.Length == 0)
            throw new FormatException("empty address");
        string host, port;
        if (text.StartsWith('['))
        {
            var close = text.IndexOf(']');
            host = close < 0 ? text[1..] : text[1..close];
            port = close < 0 ? "" : text[(close + 1)..].TrimStart(':');
        }
        else if (text.Count(c => c == ':') == 1)
        {
            var parts = text.Split(':');
            (host, port) = (parts[0], parts[1]);
        }
        else
        {
            (host, port) = (text, "");
        }
        if (host.Length == 0)
            throw new FormatException("missing host");
        if (port.Length == 0)
            return (host, Protocol.DefaultPort);
        if (!port.All(char.IsAsciiDigit) || !int.TryParse(port, out var number) || number <= 0 || number >= 65536)
            throw new FormatException($"bad port '{port}'");
        return (host, number);
    }
}

/// <summary>One connection to a phone. The session thread reads; writes from other threads are
/// serialized by a lock (SslStream allows one reader and one writer at a time).</summary>
public sealed class Session
{
    public static readonly TimeSpan ConnectTimeout = TimeSpan.FromSeconds(5);
    // The phone shows an approval prompt for up to 60 s.
    public static readonly TimeSpan ApprovalTimeout = TimeSpan.FromSeconds(75);
    // Wi-Fi drops often leave a half-open socket, so silence this long means the phone is gone.
    public const double StreamTimeout = 8.0;
    // UDP: the first FEEDBACK opens the path through the computer's firewall; the phone waits 1 s for it.
    const double PunchInterval = 0.1;
    const double PunchPhase = 2.0;
    const double FeedbackInterval = 1.0;
    const double KeyframeRetry = 0.25;
    public static readonly string[] AllTransports = ["udp", "tcp"];

    readonly string _clientName;
    readonly ISessionListener _listener;
    readonly IPairingStore _pairings;
    readonly bool _allowPairing;
    readonly Thread _thread;
    readonly Lock _writeLock = new();
    volatile bool _closed;
    volatile bool _udpReceiving;
    volatile Socket? _tcp;
    Socket? _udp;
    SslStream? _ssl;
    long _lastVideo = Stopwatch.GetTimestamp();

    public string Host { get; }
    public int Port { get; }
    public IReadOnlyList<string> Transports { get; }
    /// <summary>"udp" once UDP video arrives, else "tcp".</summary>
    public string Transport => _udpReceiving ? "udp" : "tcp";
    /// <summary>The phone's certificate fingerprint, once the TLS handshake is done.</summary>
    public string? Fingerprint { get; private set; }

    public Session(string host, int port, string clientName, ISessionListener listener, IPairingStore pairings,
        bool allowPairing = false, IReadOnlyList<string>? transports = null)
    {
        Host = host;
        Port = port;
        Transports = transports ?? AllTransports;
        _clientName = clientName;
        _listener = listener;
        _pairings = pairings;
        _allowPairing = allowPairing;
        _thread = new Thread(Run) { IsBackground = true, Name = $"session-{host}" };
    }

    static double Now() => Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency;

    void TouchVideo() => Interlocked.Exchange(ref _lastVideo, Stopwatch.GetTimestamp());

    double SinceVideo() => (Stopwatch.GetTimestamp() - Interlocked.Read(ref _lastVideo)) / (double)Stopwatch.Frequency;

    public void Start() => _thread.Start();

    public void Close()
    {
        _closed = true;
        ShutdownTcp();
    }

    public bool Join(TimeSpan timeout) => _thread.Join(timeout);

    public void SetCamera(string facing)
    {
        var message = Protocol.EncodeJson(Protocol.SetCamera, new JsonObject { ["facing"] = facing });
        ThreadPool.QueueUserWorkItem(_ => Write(message));
    }

    void Write(byte[] data)
    {
        try
        {
            lock (_writeLock)
                _ssl?.Write(data);
        }
        catch (Exception error) when (error is IOException or ObjectDisposedException or InvalidOperationException)
        {
            Log.Debug($"write to {Host} failed: {error.Message}");
        }
    }

    void ShutdownTcp()
    {
        try
        {
            _tcp?.Shutdown(SocketShutdown.Both);
        }
        catch (Exception error) when (error is SocketException or ObjectDisposedException)
        {
        }
    }

    void Run()
    {
        var reason = EndReasons.Closed;
        try
        {
            reason = RunSession();
        }
        catch (ProtocolException error)
        {
            Log.Warning($"protocol error from {Host}: {error.Message}");
            reason = EndReasons.Protocol;
        }
        catch (Exception error) when (error is IOException or SocketException or ObjectDisposedException or TimeoutException
            or InvalidOperationException or FormatException or EndOfStreamException)
        {
            Log.Info($"session with {Host} ended: {error.Message}");
            reason = EndReasons.ConnectionLost;
        }
        finally
        {
            _ssl?.Dispose();
            _tcp?.Dispose();
            _udp?.Dispose();
        }
        _listener.OnEnd(_closed ? EndReasons.Closed : reason);
    }

    static IPAddress V4(IPAddress address) => address.IsIPv4MappedToIPv6 ? address.MapToIPv4() : address;

    string RunSession()
    {
        var tcp = new Socket(SocketType.Stream, ProtocolType.Tcp);
        _tcp = tcp;
        try
        {
            using var timeout = new CancellationTokenSource(ConnectTimeout);
            tcp.ConnectAsync(Host, Port, timeout.Token).AsTask().GetAwaiter().GetResult();
        }
        catch (Exception error) when (error is SocketException or OperationCanceledException or ArgumentException)
        {
            Log.Info($"cannot connect to {Host}:{Port}: {error.Message}");
            return _closed ? EndReasons.Closed : EndReasons.ConnectFailed;
        }
        if (_closed)
            return EndReasons.Closed;
        tcp.NoDelay = true;
        tcp.ReceiveBufferSize = 1 << 20;
        tcp.ReceiveTimeout = 10_000;
        tcp.SendTimeout = 10_000;

        var ssl = new SslStream(new NetworkStream(tcp, ownsSocket: false));
        try
        {
            ssl.AuthenticateAsClient(new SslClientAuthenticationOptions
            {
                TargetHost = Host,
                EnabledSslProtocols = SslProtocols.Tls12 | SslProtocols.Tls13,
                CertificateRevocationCheckMode = X509RevocationMode.NoCheck,
                // Phones are self-signed; trust comes from the fingerprint pinned at pairing, checked below.
                RemoteCertificateValidationCallback = (_, _, _, _) => true,
            });
        }
        catch (Exception error) when (error is AuthenticationException or IOException)
        {
            Log.Info($"TLS with {Host} failed: {error.Message}");
            ssl.Dispose();
            return _closed ? EndReasons.Closed : EndReasons.Protocol;
        }
        _ssl = ssl;
        var fingerprint = Secure.Fingerprint(ssl.RemoteCertificate!.GetRawCertData());
        Fingerprint = fingerprint;
        var secret = _pairings.SecretFor(fingerprint);
        if (secret is null && !_allowPairing)
        {
            // Not a phone we paired with (or a stranger using its name): send nothing at all.
            Log.Info($"{Host} is not a paired phone ({fingerprint[..16]})");
            return EndReasons.UnknownPhone;
        }

        var offered = new JsonArray("tcp");
        if (Transports.Contains("udp") && tcp.LocalEndPoint is IPEndPoint local && V4(local.Address).AddressFamily == AddressFamily.InterNetwork)
        {
            try
            {
                var udp = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
                Net.IgnoreConnectionResets(udp);
                // Keyframes arrive as a burst of ~70 datagrams; don't drop them in the kernel.
                udp.ReceiveBufferSize = 4 << 20;
                udp.Bind(new IPEndPoint(V4(local.Address), 0));
                _udp = udp;
                offered.Insert(0, "udp");
            }
            catch (SocketException error)
            {
                Log.Info($"UDP unavailable: {error.Message}");
            }
        }
        var hello = new JsonObject { ["protocol"] = Protocol.Version, ["clientName"] = _clientName, ["transports"] = offered };
        if (secret is not null)
            hello["secret"] = Convert.ToHexStringLower(secret);
        lock (_writeLock)
            ssl.Write(Protocol.EncodeJson(Protocol.Hello, hello));

        var reader = new MessageReader();
        var buffer = new byte[256 * 1024];
        tcp.ReceiveTimeout = (int)ApprovalTimeout.TotalMilliseconds;
        while (true)
        {
            int read;
            try
            {
                read = ssl.Read(buffer);
            }
            catch (IOException error) when (error.InnerException is SocketException { SocketErrorCode: SocketError.TimedOut })
            {
                throw new TimeoutException($"nothing from the phone for {tcp.ReceiveTimeout / 1000} s");
            }
            if (read == 0)
                throw new EndOfStreamException("connection closed");
            foreach (var message in reader.Feed(buffer.AsSpan(0, read)))
            {
                var end = Handle(message, ssl, tcp, fingerprint, secret);
                if (end is not null)
                    return end;
                if (message.Type == Protocol.Accept)
                {
                    // With UDP video the TLS connection can be quiet; the UDP loop watches liveness.
                    tcp.ReceiveTimeout = _udpThread is not null ? 0 : (int)(StreamTimeout * 1000);
                }
            }
        }
    }

    Thread? _udpThread;

    static int Int(JsonObject body, string key, int fallback)
    {
        try
        {
            return body[key] is JsonValue value ? (value.TryGetValue<int>(out var number) ? number : int.Parse(value.ToString())) : fallback;
        }
        catch (FormatException)
        {
            throw new ProtocolException($"bad {key} in message");
        }
    }

    static string Str(JsonObject body, string key, string fallback) => body[key] is JsonValue value ? value.ToString() : fallback;

    string? Handle(Message message, SslStream ssl, Socket tcp, string fingerprint, byte[]? secret)
    {
        switch (message.Type)
        {
            case Protocol.Pending:
                _listener.OnPending(Secure.PairingCode(fingerprint));
                break;
            case Protocol.Accept:
            {
                var body = message.Json();
                var codec = body["codec"] is JsonValue c ? c.ToString() : null;
                if (codec != "h264")
                    throw new ProtocolException($"unsupported codec '{codec}'");
                var info = new StreamInfo(Str(body, "deviceId", ""), Str(body, "deviceName", "Phone"), Int(body, "width", 1280),
                    Int(body, "height", 720), Int(body, "fps", 30), Str(body, "facing", "front"));
                var newSecret = body["secret"] is JsonValue s && s.TryGetValue<string>(out var text) ? text : null;
                if (secret is null)
                {
                    if (newSecret is not { Length: 64 })
                        throw new ProtocolException("phone accepted without pairing");
                    _pairings.Pair(fingerprint, Convert.FromHexString(newSecret), info.DeviceId, info.DeviceName);
                }
                else
                {
                    _pairings.Connected(fingerprint, info.DeviceName);
                }
                _listener.OnAccept(info);
                TouchVideo();
                byte[] key;
                try
                {
                    key = Convert.FromHexString(Str(body, "udpKey", ""));
                }
                catch (FormatException)
                {
                    key = [];
                }
                if (_udp is not null && body.ContainsKey("udpPort") && key.Length == 32 && tcp.RemoteEndPoint is IPEndPoint peer)
                {
                    var port = Int(body, "udpPort", 0);
                    _udpThread = new Thread(() => UdpLoop(_udp, V4(peer.Address), port, key)) { IsBackground = true, Name = "udp" };
                    _udpThread.Start();
                }
                break;
            }
            case Protocol.Video:
                TouchVideo();
                _listener.OnFrame(Protocol.DecodeVideo(message.Payload));
                break;
            case Protocol.Camera:
            {
                var body = message.Json();
                var rotation = Int(body, "rotation", 0);
                if (rotation is 0 or 90 or 180 or 270)
                    _listener.OnCamera(rotation, Str(body, "facing", "front"));
                break;
            }
            case Protocol.End:
            {
                var reason = Str(message.Json(), "reason", "stopped");
                if (reason == EndReasons.Forgotten)
                    // The phone no longer knows our secret; pairing again needs the user's click.
                    _pairings.Forget(fingerprint);
                return reason;
            }
            // Unknown types are skipped so newer phones can add messages.
        }
        return null;
    }

    /// <summary>Receive UDP video, send FEEDBACK (heartbeat and keyframe requests), and watch liveness
    /// for both transports: if the phone fell back to TCP, its video still updates the last-video time.</summary>
    void UdpLoop(Socket udp, IPAddress peer, int port, byte[] key)
    {
        using var sealer = Sealer.ForComputer(key);
        var phone = new IPEndPoint(peer, port);
        udp.ReceiveTimeout = 20;
        var reassembler = new Reassembler();
        var started = Now();
        var nextFeedback = started;
        var lastRequest = 0.0;
        var receiving = false;
        var firstPacket = 0.0;
        var buffer = new byte[2048];
        while (!_closed)
        {
            var now = Now();
            // Ask only after a loss, or if the phone's first keyframe never completes.
            var wantKeyframe = receiving && reassembler.WaitingForKeyframe && (reassembler.Losses > 0 || now - firstPacket > 1.0)
                && now - lastRequest > KeyframeRetry;
            if ((receiving || now - started < PunchPhase) && (now >= nextFeedback || wantKeyframe))
            {
                if (wantKeyframe)
                    lastRequest = now;
                var feedback = Protocol.EncodeFeedback(wantKeyframe ? Protocol.FeedbackKeyframe : (byte)0, reassembler.LastFrame, reassembler.Losses);
                try
                {
                    udp.SendTo(sealer.Seal(feedback), phone);
                }
                catch (SocketException error)
                {
                    Log.Debug($"feedback failed: {error.Message}");
                }
                nextFeedback = now + (receiving ? FeedbackInterval : PunchInterval);
            }
            List<VideoFrame> frames;
            try
            {
                EndPoint from = new IPEndPoint(IPAddress.Any, 0);
                var length = udp.ReceiveFrom(buffer, ref from);
                var plaintext = V4(((IPEndPoint)from).Address).Equals(peer) ? sealer.Open(buffer.AsSpan(0, length)) : null;
                var fragment = plaintext is null ? null : Protocol.ParseFragment(plaintext);
                if (fragment is null)
                    continue;
                if (!receiving)
                {
                    receiving = true;
                    firstPacket = Now();
                    _udpReceiving = true;
                    Log.Info("receiving video over UDP");
                }
                TouchVideo();
                frames = reassembler.Add(fragment, Now());
            }
            catch (SocketException error) when (error.SocketErrorCode is SocketError.TimedOut or SocketError.WouldBlock)
            {
                frames = reassembler.Poll(Now());
            }
            catch (Exception error) when (error is SocketException or ObjectDisposedException)
            {
                return; // socket closed by Run
            }
            var missing = reassembler.Missing(Now());
            if (missing.Count > 0)
            {
                try
                {
                    udp.SendTo(sealer.Seal(Protocol.EncodeNack(missing)), phone);
                }
                catch (SocketException error)
                {
                    Log.Debug($"nack failed: {error.Message}");
                }
            }
            foreach (var frame in frames)
                _listener.OnFrame(frame);
            if (SinceVideo() > StreamTimeout)
            {
                Log.Info($"no video from {peer} for {StreamTimeout:0} s");
                ShutdownTcp();
                return;
            }
        }
    }
}
