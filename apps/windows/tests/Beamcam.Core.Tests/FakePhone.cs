using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json.Nodes;

namespace Beamcam.Tests;

/// <summary>A TLS phone that serves one connection: read HELLO, send the scripted messages, like
/// fake_phone() in apps/linux/tests/test_client.py.</summary>
sealed class FakePhone
{
    public static readonly X509Certificate2 Certificate = MakeCertificate();
    public static readonly string Fingerprint = Secure.Fingerprint(Certificate.RawData);

    static X509Certificate2 MakeCertificate()
    {
        using var key = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        var request = new CertificateRequest("CN=Beamcam", key, HashAlgorithmName.SHA256);
        using var ephemeral = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(1));
        // Windows' TLS stack cannot use an in-memory key; a PKCS#12 round trip gives it a usable one.
        return X509CertificateLoader.LoadPkcs12(ephemeral.Export(X509ContentType.Pkcs12), null);
    }

    readonly TcpListener _listener = new(IPAddress.Loopback, 0);
    public int Port => ((IPEndPoint)_listener.LocalEndpoint).Port;
    public JsonObject? Hello { get; private set; }
    public List<Message> Later { get; } = [];
    public readonly ManualResetEventSlim HelloReceived = new();

    public FakePhone(IEnumerable<byte[]> script, bool holdOpen = false)
    {
        _listener.Start(1);
        var steps = script.ToList();
        new Thread(() => Serve(steps, holdOpen)) { IsBackground = true }.Start();
    }

    void Serve(List<byte[]> script, bool holdOpen)
    {
        try
        {
            using var client = _listener.AcceptTcpClient();
            using var ssl = new SslStream(client.GetStream());
            ssl.AuthenticateAsServer(Certificate);
            var reader = new MessageReader();
            var buffer = new byte[65536];
            var messages = new Queue<Message>();
            Message Next()
            {
                while (messages.Count == 0)
                {
                    var read = ssl.Read(buffer);
                    if (read == 0)
                        throw new EndOfStreamException();
                    foreach (var m in reader.Feed(buffer.AsSpan(0, read)))
                        messages.Enqueue(m);
                }
                return messages.Dequeue();
            }
            Hello = Next().Json();
            HelloReceived.Set();
            foreach (var chunk in script)
                ssl.Write(chunk);
            ssl.Flush();
            if (holdOpen)
                while (true)
                {
                    var message = Next();
                    lock (Later)
                        Later.Add(message);
                }
        }
        catch (Exception error) when (error is IOException or EndOfStreamException or SocketException or ObjectDisposedException
            or System.Security.Authentication.AuthenticationException)
        {
        }
        finally
        {
            _listener.Stop();
        }
    }

    public static byte[] Accept(Action<JsonObject>? extra = null)
    {
        var body = new JsonObject
        {
            ["protocol"] = 2, ["deviceId"] = "phone-1", ["deviceName"] = "Pixel", ["codec"] = "h264", ["width"] = 1280, ["height"] = 720, ["fps"] = 30,
        };
        extra?.Invoke(body);
        return Protocol.EncodeJson(Protocol.Accept, body);
    }

    public static readonly byte[] EndStopped = Protocol.EncodeJson(Protocol.End, new JsonObject { ["reason"] = "stopped" });
}

sealed class MemoryPairings(Dictionary<string, byte[]>? known = null) : IPairingStore
{
    public readonly Dictionary<string, byte[]> Secrets = known ?? [];
    public readonly List<(string, string, string)> Paired = [];
    public readonly List<(string, string)> Connections = [];

    public byte[]? SecretFor(string fingerprint) => Secrets.GetValueOrDefault(fingerprint);

    public void Pair(string fingerprint, byte[] secret, string phoneId, string name)
    {
        Secrets[fingerprint] = secret;
        Paired.Add((fingerprint, phoneId, name));
    }

    public void Connected(string fingerprint, string name) => Connections.Add((fingerprint, name));

    public void Forget(string fingerprint) => Secrets.Remove(fingerprint);
}

sealed class Recorder : ISessionListener
{
    public readonly List<object[]> Events = [];
    public readonly ManualResetEventSlim Done = new();

    void Add(params object[] e)
    {
        lock (Events)
            Events.Add(e);
    }

    public List<object[]> Snapshot()
    {
        lock (Events)
            return [.. Events];
    }

    public void OnPending(string code) => Add("pending", code);
    public void OnAccept(StreamInfo info) => Add("accept", info.DeviceName, info.Width, info.Height);
    public void OnFrame(VideoFrame frame) => Add("frame", frame.Keyframe, frame.Data);
    public void OnCamera(int rotation, string facing) => Add("camera", rotation, facing);

    public void OnEnd(string reason)
    {
        Add("end", reason);
        Done.Set();
    }
}
