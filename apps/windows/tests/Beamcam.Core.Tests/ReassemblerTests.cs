namespace Beamcam.Tests;

// Mirrors apps/linux/tests/test_reassembly.py.
public class ReassemblerTests
{
    const byte Key = Protocol.FlagKeyframe;

    static List<Fragment> Fragments(long frameId, byte flags = 0, int size = 3000)
    {
        var data = Enumerable.Repeat((byte)(frameId % 256), size).ToArray();
        return Protocol.EncodeFragments(frameId, frameId * 33_000, flags, data).Select(d => Protocol.ParseFragment(d)!).ToList();
    }

    static List<long> Feed(Reassembler r, IEnumerable<Fragment> fragments, double now = 0.0)
    {
        var output = new List<VideoFrame>();
        foreach (var f in fragments)
            output.AddRange(r.Add(f, now));
        return output.Select(f => f.PtsUs / 33_000).ToList();
    }

    [Fact]
    public void InOrderFrames()
    {
        var r = new Reassembler();
        Assert.Equal([0, 1, 2], Feed(r, [.. Fragments(0, Key), .. Fragments(1), .. Fragments(2)]));
        Assert.False(r.WaitingForKeyframe);
    }

    [Fact]
    public void WaitsForFirstKeyframe()
    {
        var r = new Reassembler();
        Assert.Empty(Feed(r, Fragments(5)));
        Assert.True(r.WaitingForKeyframe);
        Assert.Equal([6, 7], Feed(r, [.. Fragments(6, Key), .. Fragments(7)]));
    }

    [Fact]
    public void ReorderedFragmentsAndFrames()
    {
        var r = new Reassembler();
        var (a, b) = (Fragments(0, Key), Fragments(1));
        Assert.Equal([0, 1], Feed(r, [b[1], a[2], b[0], a[0], b[2], a[1]]));
    }

    [Fact]
    public void LostFragmentSkipsToNextKeyframe()
    {
        var r = new Reassembler();
        Feed(r, Fragments(0, Key));
        var damaged = Fragments(1)[1..]; // first piece of frame 1 never arrives
        Assert.Empty(Feed(r, [.. damaged, .. Fragments(2), .. Fragments(3)], now: 1.0));
        // Newer frames waited past the reorder window: frame 1 is lost, P-frames 2-3 are held back.
        Assert.Empty(r.Poll(1.0 + Reassembler.LossTimeout * 2));
        Assert.True(r.WaitingForKeyframe);
        Assert.Equal(1, r.Losses);
        Assert.Equal([5, 6], Feed(r, [.. Fragments(4), .. Fragments(5, Key), .. Fragments(6)], now: 2.0));
    }

    [Fact]
    public void LateFragmentWithinWindowIsNotALoss()
    {
        var r = new Reassembler();
        Feed(r, Fragments(0, Key));
        var f1 = Fragments(1);
        Assert.Empty(Feed(r, [.. f1[1..], .. Fragments(2)], now: 1.0));
        Assert.Equal([1, 2], Feed(r, f1[..1], now: 1.0 + Reassembler.LossTimeout / 2));
        Assert.Equal(0, r.Losses);
    }

    [Fact]
    public void MissingPiecesAreReportedOncePerRetry()
    {
        var r = new Reassembler();
        Feed(r, Fragments(0, Key));
        var f1 = Fragments(1);
        Feed(r, [f1[0], f1[2]], now: 1.0);
        Assert.Equal([(1L, 1)], r.Missing(1.0));
        Assert.Empty(r.Missing(1.0 + Reassembler.NackRetry / 2));
        Assert.Equal([(1L, 1)], r.Missing(1.0 + Reassembler.NackRetry));
    }

    [Fact]
    public void TrailingPiecesAndWholeFramesNeedANewerPacket()
    {
        var r = new Reassembler();
        Feed(r, Fragments(0, Key));
        Feed(r, Fragments(1)[..1], now: 1.0);
        Assert.Empty(r.Missing(1.0)); // the rest may still be on its way
        Feed(r, Fragments(3)[..1], now: 1.01);
        Assert.Equal([(1L, 1), (1L, 2), (2L, Protocol.NackWholeFrame)], r.Missing(1.01));
    }

    [Fact]
    public void ResentPieceRecoversWithoutAKeyframe()
    {
        var r = new Reassembler();
        Feed(r, Fragments(0, Key));
        var f1 = Fragments(1);
        Assert.Empty(Feed(r, [.. f1[1..], .. Fragments(2)], now: 1.0));
        Assert.Equal([(1L, 0)], r.Missing(1.0));
        Assert.Equal([1, 2], Feed(r, f1[..1], now: 1.0 + Reassembler.LossTimeout * 0.8));
        Assert.Equal((0, false), (r.Losses, r.WaitingForKeyframe));
    }

    [Fact]
    public void OutageSkipsToKeyframeInsteadOfResendingStaleVideo()
    {
        var r = new Reassembler();
        Feed(r, Fragments(0, Key));
        Assert.Empty(Feed(r, Fragments(2 + Reassembler.MaxRecoverFrames), now: 1.0));
        Assert.True(r.WaitingForKeyframe);
        Assert.Empty(r.Missing(1.0)); // nothing old is worth asking for
    }

    [Fact]
    public void DuplicatesAreHarmless()
    {
        var r = new Reassembler();
        var f0 = Fragments(0, Key);
        Assert.Equal([0, 1], Feed(r, [.. f0, .. f0, .. Fragments(1)]));
    }

    [Fact]
    public void FrameDataIsReassembledInOrder()
    {
        var r = new Reassembler();
        var data = Enumerable.Range(0, 5000).Select(i => (byte)(i * 7)).ToArray();
        var parts = Protocol.EncodeFragments(0, 0, Key, data).Select(d => Protocol.ParseFragment(d)!).Reverse().ToList();
        var frames = parts.SelectMany(p => r.Add(p, 0)).ToList();
        Assert.Equal(data, Assert.Single(frames).Data);
    }
}
