using System.Collections.Concurrent;
using System.Text.Json.Nodes;

namespace Beamcam.Tests;

public class ControllerTests
{
    static readonly byte[] SecretBytes = Enumerable.Range(0, 32).Select(i => (byte)i).ToArray();

    sealed class FakeOutput(bool fail) : IVideoOutput
    {
        public StreamInfo? Started;
        public readonly List<VideoFrame> Frames = [];
        public int Rotation = -1;
        public bool Disposed;

        public void Start(StreamInfo info)
        {
            if (fail)
                throw new VideoException("no decoder");
            Started = info;
        }

        public void Push(VideoFrame frame) => Frames.Add(frame);
        public void SetRotation(int degrees) => Rotation = degrees;
        public long FramesDecoded => Frames.Count;
        public void Dispose() => Disposed = true;
    }

    sealed class Events : IControllerEvents
    {
        public readonly BlockingCollection<string> Log = [];
        public void OnPending(string code) => Log.Add("pending");
        public void OnStreaming(StreamInfo info) => Log.Add($"streaming {info.DeviceName}");
        public void OnCamera(string facing) => Log.Add($"camera {facing}");
        public void OnEnded(string reason) => Log.Add($"ended {reason}");
    }

    static (FakeOutput Output, List<string> Log) Run(bool fail, params byte[][] script)
    {
        var phone = new FakePhone(script);
        FakeOutput? output = null;
        var events = new Events();
        var controller = new Controller("127.0.0.1", phone.Port, "pc", _ => output = new FakeOutput(fail), events,
            new MemoryPairings(new() { [FakePhone.Fingerprint] = SecretBytes }), action => action());
        controller.Start();
        var log = new List<string>();
        while (events.Log.TryTake(out var line, TimeSpan.FromSeconds(10)))
        {
            log.Add(line);
            if (line.StartsWith("ended"))
                break;
        }
        return (output!, log);
    }

    [Fact]
    public void StreamsIntoTheOutputWithTheLatestRotation()
    {
        var (output, log) = Run(false,
            Protocol.EncodeJson(Protocol.Camera, new JsonObject { ["rotation"] = 270, ["facing"] = "front" }),
            FakePhone.Accept(),
            Protocol.EncodeVideo(1, Protocol.FlagKeyframe, [1]),
            Protocol.EncodeVideo(2, 0, [2]),
            FakePhone.EndStopped);
        Assert.Equal(["camera front", "streaming Pixel", "ended stopped"], log);
        Assert.Equal(1280, output.Started!.Width);
        Assert.Equal(270, output.Rotation);
        Assert.Equal([1, 2], output.Frames.Select(f => f.Data[0]));
        Assert.True(output.Disposed);
    }

    [Fact]
    public void OutputThatCannotStartEndsWithAVideoError()
    {
        var (_, log) = Run(true, FakePhone.Accept(), Protocol.EncodeVideo(1, Protocol.FlagKeyframe, [1]));
        Assert.Equal(["ended " + EndReasons.VideoError], log);
    }
}
