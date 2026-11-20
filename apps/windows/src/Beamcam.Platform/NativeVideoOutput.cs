namespace Beamcam;

/// <summary>Decodes with Windows' H.264 decoder (Media Foundation) on its own thread and feeds the
/// virtual camera and the preview. Prefers dropping frames over queueing them: when decoding falls
/// behind, everything queued is dropped and decoding restarts at the next keyframe.</summary>
public sealed class NativeVideoOutput(VirtualCamera? camera, Action<string> onError) : IVideoOutput
{
    public const int PreviewWidth = VirtualCamera.Width / 2;
    public const int PreviewHeight = VirtualCamera.Height / 2;
    const int MaxQueued = 8;
    const int MaxErrors = 60;

    readonly Lock _lock = new();
    readonly Queue<VideoFrame> _queue = new();
    readonly AutoResetEvent _wake = new(false);
    Thread? _worker;
    IntPtr _video;
    volatile int _rotation;
    volatile bool _stopping;
    bool _skipToKeyframe = true;

    public void Start(StreamInfo info)
    {
        var hr = Native.BcVideoCreate(info.Width, info.Height, info.Fps, camera?.Handle ?? IntPtr.Zero, out _video);
        if (hr < 0)
            throw new VideoException($"Windows’ video decoder isn’t available (error 0x{hr:x8}).");
        _worker = new Thread(Work) { IsBackground = true, Name = "decode" };
        _worker.Start();
    }

    public void Push(VideoFrame frame)
    {
        lock (_lock)
        {
            if (_stopping)
                return;
            if (_queue.Count >= MaxQueued)
            {
                Log.Info("decoding fell behind; skipping to the next keyframe");
                _queue.Clear();
                _skipToKeyframe = true;
            }
            if (_skipToKeyframe && !frame.Keyframe)
                return;
            _skipToKeyframe = false;
            _queue.Enqueue(frame);
        }
        _wake.Set();
    }

    public void SetRotation(int degrees) => _rotation = degrees;

    public long FramesDecoded
    {
        get
        {
            lock (_lock)
                return _video == IntPtr.Zero ? 0 : Native.BcVideoFrames(_video);
        }
    }

    void Work()
    {
        var errors = 0;
        while (!_stopping)
        {
            VideoFrame? frame;
            lock (_lock)
                _queue.TryDequeue(out frame);
            if (frame is null)
            {
                _wake.WaitOne(100);
                continue;
            }
            var hr = Native.BcVideoPush(_video, frame.Data, frame.Data.Length, frame.PtsUs, _rotation);
            if (hr >= 0)
            {
                errors = 0;
                continue;
            }
            Log.Debug($"decoder rejected a frame: 0x{hr:x8}");
            if (++errors == MaxErrors)
                onError($"Windows’ video decoder failed (error 0x{hr:x8}).");
        }
    }

    /// <summary>Copies the newest preview picture (PreviewWidth x PreviewHeight BGRA) if it is newer
    /// than <paramref name="seen"/>; returns its number.</summary>
    public long CopyPreview(long seen, IntPtr bgra, int stride)
    {
        lock (_lock)
            return _video == IntPtr.Zero ? seen : Native.BcVideoCopyPreview(_video, seen, bgra, stride);
    }

    /// <summary>The newest output frame as PNG (1280x720), or null before the first one.</summary>
    public byte[]? SnapshotPng()
    {
        var pixels = new byte[VirtualCamera.Width * VirtualCamera.Height * 4];
        lock (_lock)
        {
            if (_video == IntPtr.Zero || Native.BcVideoSnapshot(_video, pixels, VirtualCamera.Width * 4) != 0)
                return null;
        }
        return Png.Encode(VirtualCamera.Width, VirtualCamera.Height, pixels, VirtualCamera.Width * 4);
    }

    public void Dispose()
    {
        _stopping = true;
        _wake.Set();
        _worker?.Join();
        lock (_lock)
        {
            if (_video != IntPtr.Zero)
                Native.BcVideoDestroy(_video);
            _video = IntPtr.Zero;
        }
        // Other apps must not keep seeing the last picture of someone who disconnected.
        camera?.ShowIdle();
    }
}
