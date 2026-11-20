using System.Collections.Concurrent;
using System.Diagnostics;
using Beamcam;

const string Usage = """
    Use your Android phone as a webcam.

    usage: beamcam-cli [-v] discover [--timeout SECONDS]
           beamcam-cli [-v] stream ADDRESS [--no-camera] [--frames N] [--snapshot FILE.png]
                                         [--camera front|back] [--tcp-only]

      discover     list phones on the local network
      stream       stream from a phone (e.g. 192.168.1.23 or 192.168.1.23:47800) into the
                   "Beamcam" camera; --no-camera decodes only
    """;

var arguments = args.ToList();
if (arguments.Remove("-v") | arguments.Remove("--verbose"))
    Log.Verbose = true;
Log.AddSink(line => Console.Error.WriteLine(line));
if (arguments.Count == 0 || arguments[0] is "-h" or "--help")
{
    Console.WriteLine(Usage);
    return arguments.Count == 0 ? 2 : 0;
}

string? Option(string name)
{
    var index = arguments.IndexOf(name);
    if (index < 0)
        return null;
    var value = index + 1 < arguments.Count ? arguments[index + 1] : throw new ArgumentException($"{name} needs a value");
    arguments.RemoveRange(index, 2);
    return value;
}

bool Flag(string name) => arguments.Remove(name);

try
{
    return arguments[0] switch
    {
        "discover" => Discover(double.Parse(Option("--timeout") ?? "5", System.Globalization.CultureInfo.InvariantCulture)),
        "stream" => Stream(),
        _ => throw new ArgumentException($"unknown command {arguments[0]}"),
    };
}
catch (ArgumentException error)
{
    Console.Error.WriteLine(error.Message);
    Console.Error.WriteLine(Usage);
    return 2;
}

static int Discover(double timeout)
{
    var found = new ConcurrentDictionary<string, Phone>();
    var browser = new MdnsBrowser(phones =>
    {
        foreach (var phone in phones)
            if (found.TryAdd(phone.Id, phone))
                Console.WriteLine($"{phone.Name}\t{phone.Host}:{phone.Port}\tid={phone.Id}");
    });
    browser.Start();
    Thread.Sleep(TimeSpan.FromSeconds(timeout));
    browser.Stop();
    if (!found.IsEmpty)
        return 0;
    Console.Error.WriteLine("No phones found. Is Beamcam open on the phone, on the same Wi-Fi?");
    return 1;
}

int Stream()
{
    var noCamera = Flag("--no-camera");
    var tcpOnly = Flag("--tcp-only");
    var frames = int.Parse(Option("--frames") ?? "0", System.Globalization.CultureInfo.InvariantCulture);
    var snapshot = Option("--snapshot");
    var facing = Option("--camera");
    if (facing is not (null or "front" or "back"))
        throw new ArgumentException("--camera is front or back");
    if (arguments.Count != 2)
        throw new ArgumentException("stream needs an address");
    (string host, int port) target;
    try
    {
        target = Address.Parse(arguments[1]);
    }
    catch (FormatException error)
    {
        Console.Error.WriteLine($"Bad address: {error.Message}");
        return 2;
    }

    VirtualCamera? camera = null;
    if (!noCamera)
    {
        camera = VirtualCamera.TryCreate(out var problem);
        if (camera is null)
        {
            Console.Error.WriteLine(problem);
            return 2;
        }
    }

    var loop = new BlockingCollection<Action>();
    var exit = 0;
    var finished = false;
    NativeVideoOutput? output = null;
    var config = Config.Load();
    Controller? controller = null;
    var events = new Events(
        code => Console.WriteLine($"Waiting for you to tap Allow on the phone. It shows the code {code}."),
        info =>
        {
            Console.WriteLine($"Streaming from {info.DeviceName} ({info.Width}x{info.Height}) to {(camera is null ? "nowhere" : "the Beamcam camera")}");
            if (facing is not null)
                controller!.SetCamera(facing);
        },
        reason =>
        {
            if (reason != EndReasons.Closed)
            {
                Console.Error.WriteLine(EndReasons.Message(reason));
                if (controller!.OutputError is { } detail)
                    Console.Error.WriteLine(detail);
                exit = 1;
            }
            finished = true;
        });
    controller = new Controller(target.host, target.port, Config.ComputerName(),
        onError => output = new NativeVideoOutput(camera, onError), events, config, loop.Add,
        // Running the command is the user asking to connect, so a new phone may be paired.
        allowPairing: true, transports: tcpOnly ? ["tcp"] : null);

    var stopped = false;
    void Finish()
    {
        if (stopped)
            return;
        stopped = true;
        if (snapshot is not null)
        {
            if (output?.SnapshotPng() is { } png)
            {
                File.WriteAllBytes(snapshot, png);
                Console.WriteLine($"Saved {snapshot}");
            }
            else
            {
                Console.Error.WriteLine("No frame to save.");
            }
        }
        controller.Stop();
    }

    Console.CancelKeyPress += (_, e) =>
    {
        e.Cancel = true;
        loop.Add(Finish);
    };
    var transportShown = false;
    var clock = Stopwatch.StartNew();
    controller.Start();
    while (!finished)
    {
        if (loop.TryTake(out var action, 100))
            action();
        var decoded = output?.FramesDecoded ?? 0;
        if (decoded > 0 && !transportShown)
        {
            // Only now is it known which way the video came (cameras differ in start-up time).
            transportShown = true;
            Console.WriteLine($"Video transport: {controller.Session.Transport}");
            clock.Restart();
        }
        if (frames > 0 && decoded >= frames && !stopped)
        {
            Console.WriteLine($"Decoded {decoded} frames in {clock.Elapsed.TotalSeconds:0.0} s.");
            Finish();
        }
    }
    controller.Session.Join(TimeSpan.FromSeconds(2));
    camera?.Dispose();
    return exit;
}

sealed class Events(Action<string> pending, Action<StreamInfo> streaming, Action<string> ended) : IControllerEvents
{
    public void OnPending(string code) => pending(code);
    public void OnStreaming(StreamInfo info) => streaming(info);
    public void OnCamera(string facing) { }
    public void OnEnded(string reason) => ended(reason);
}
