namespace Beamcam;

/// <summary>
/// Turns UDP fragments back into frames, in order. Same rules as apps/linux/src/beamcam/reassembly.py.
///
/// Missing pieces are reported by <see cref="Missing"/> as soon as a later packet shows the gap, so the
/// caller can ask the phone to resend them (NACK). A frame still incomplete after LossTimeout is lost.
/// After a loss the following P-frames would decode as smeared garbage, so frames are held back until
/// the next keyframe; while <see cref="WaitingForKeyframe"/> is true the caller should keep asking for one.
/// </summary>
public sealed class Reassembler(double lossTimeout = Reassembler.LossTimeout)
{
    // How long the stream waits for a missing piece once newer packets have arrived. Covers a few
    // resend round trips on Wi-Fi (5-30 ms each); after that a keyframe recovers faster.
    public const double LossTimeout = 0.15;
    public const double NackRetry = 0.03;
    // A gap of more frames than this is an outage, not packet loss: resending would only deliver stale
    // video in a burst, so give up at once and recover with a keyframe. About 165 ms at 30 fps.
    public const int MaxRecoverFrames = 5;
    public const int MaxPending = 120;

    sealed class Pending(int count, byte flags, long ptsUs, double firstSeen)
    {
        public readonly int Count = count;
        public readonly byte Flags = flags;
        public readonly long PtsUs = ptsUs;
        public readonly double FirstSeen = firstSeen;
        public readonly Dictionary<int, byte[]> Pieces = [];
        public bool Complete => Pieces.Count == Count;

        public VideoFrame Frame()
        {
            var size = 0;
            for (var i = 0; i < Count; i++)
                size += Pieces[i].Length;
            var data = new byte[size];
            var offset = 0;
            for (var i = 0; i < Count; i++)
            {
                Pieces[i].CopyTo(data, offset);
                offset += Pieces[i].Length;
            }
            return new VideoFrame(PtsUs, Flags, data);
        }
    }

    readonly Dictionary<long, Pending> _pending = [];
    readonly Dictionary<long, double> _gapSeen = []; // frame id -> when a later packet first showed it incomplete
    readonly Dictionary<(long, int), double> _nacked = [];
    long? _newest;
    long? _next; // next frame id to deliver; null until a keyframe arrives

    public int Losses { get; private set; }
    public long LastFrame { get; private set; }
    public bool WaitingForKeyframe => _next is null;

    public List<VideoFrame> Add(Fragment fragment, double now)
    {
        var fid = fragment.FrameId;
        if (_next is { } next && fid < next)
            return Poll(now); // late duplicate or part of a frame we gave up on
        if (!_pending.TryGetValue(fid, out var pending))
        {
            if (_pending.Count >= MaxPending)
                _pending.Remove(_pending.Keys.Min());
            pending = _pending[fid] = new Pending(fragment.Count, fragment.Flags, fragment.PtsUs, now);
        }
        if (fragment.Count == pending.Count)
            pending.Pieces[fragment.Index] = fragment.Data;
        if (_newest is null || fid > _newest)
            _newest = fid;
        return Poll(now);
    }

    /// <summary>(frame id, index) pieces that a later packet proves missing and that were not asked for
    /// in the last NackRetry. Index NackWholeFrame means no piece of that frame arrived.</summary>
    public List<(long FrameId, int Index)> Missing(double now)
    {
        var output = new List<(long, int)>();
        if (_newest is not { } newest)
            return output;
        var start = _next ?? (_pending.Count > 0 ? _pending.Keys.Min() : newest);
        for (var fid = Math.Max(start, newest - MaxRecoverFrames); fid <= newest; fid++)
        {
            List<int> gaps;
            if (!_pending.TryGetValue(fid, out var pending))
            {
                gaps = fid < newest ? [Protocol.NackWholeFrame] : [];
            }
            else if (pending.Complete)
            {
                continue;
            }
            else
            {
                var lastSeen = pending.Pieces.Keys.Max();
                gaps = [];
                for (var i = 0; i < pending.Count; i++)
                    if (!pending.Pieces.ContainsKey(i) && (i < lastSeen || fid < newest))
                        gaps.Add(i);
            }
            if (gaps.Count == 0)
                continue;
            _gapSeen.TryAdd(fid, now);
            foreach (var index in gaps)
            {
                var asked = _nacked.TryGetValue((fid, index), out var when) ? when : -1.0;
                if (now - asked >= NackRetry)
                {
                    _nacked[(fid, index)] = now;
                    output.Add((fid, index));
                }
            }
        }
        return output;
    }

    public List<VideoFrame> Poll(double now)
    {
        var output = new List<VideoFrame>();
        while (true)
        {
            if (_next is not { } next)
            {
                long? keyframe = null;
                foreach (var (fid, p) in _pending)
                    if (p.Complete && (p.Flags & Protocol.FlagKeyframe) != 0 && (keyframe is null || fid < keyframe))
                        keyframe = fid;
                if (keyframe is null)
                    break;
                SkipTo(keyframe.Value);
                continue;
            }
            if (_pending.TryGetValue(next, out var pending) && pending.Complete)
            {
                _pending.Remove(next);
                Forget(next);
                output.Add(pending.Frame());
                LastFrame = next;
                _next = next + 1;
                continue;
            }
            if (Overdue(next, now))
            {
                Losses++;
                _next = null;
                continue;
            }
            break;
        }
        return output;
    }

    /// <summary>The awaited frame is given up once newer packets have shown it incomplete for LossTimeout.</summary>
    bool Overdue(long next, double now)
    {
        if (_newest is not { } newest || newest <= next)
            return false;
        if (newest - next > MaxRecoverFrames)
            return true;
        if (!_gapSeen.TryGetValue(next, out var since))
        {
            since = now;
            foreach (var (fid, p) in _pending)
                if (fid > next && p.FirstSeen < since)
                    since = p.FirstSeen;
        }
        return now - since > lossTimeout;
    }

    void SkipTo(long frameId)
    {
        foreach (var fid in _pending.Keys.Where(f => f < frameId).ToList())
            _pending.Remove(fid);
        foreach (var fid in _gapSeen.Keys.Where(f => f < frameId).ToList())
            Forget(fid);
        _next = frameId;
    }

    void Forget(long frameId)
    {
        _gapSeen.Remove(frameId);
        foreach (var key in _nacked.Keys.Where(k => k.Item1 <= frameId).ToList())
            _nacked.Remove(key);
    }
}
