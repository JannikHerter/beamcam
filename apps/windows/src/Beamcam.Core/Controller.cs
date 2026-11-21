namespace Beamcam;

/// <summary>Where decoded video goes: the virtual camera and the preview. Implemented with Media
/// Foundation in Beamcam.Platform; tests use fakes.</summary>
public interface IVideoOutput : IDisposable
{
    /// <summary>Called once, on the session thread, when the phone accepted. Throws VideoException.</summary>
    void Start(StreamInfo info);
    /// <summary>Must not block: called on the network threads.</summary>
    void Push(VideoFrame frame);
    void SetRotation(int degrees);
    long FramesDecoded { get; }
}

public sealed class VideoException(string message) : Exception(message);

public interface IControllerEvents
{
    void OnPending(string code);
    void OnStreaming(StreamInfo info);
    void OnCamera(string facing);
    void OnEnded(string reason);
}

/// <summary>Ties a phone Session to a video output, like apps/linux/src/beamcam/controller.py. Owner
/// callbacks always run through <c>post</c>, which puts them on the owner's thread (the window's
/// dispatcher, or the command line's loop).</summary>
public sealed class Controller : ISessionListener
{
    readonly IControllerEvents _events;
    readonly Func<Action<string>, IVideoOutput> _makeOutput;
    readonly Action<Action> _post;
    readonly Lock _lock = new();
    IVideoOutput? _output;
    int _rotation;
    bool _ended;

    public Session Session { get; }
    public StreamInfo? Info { get; private set; }
    /// <summary>Why the video output failed, if that is what ended the session.</summary>
    public string? OutputError { get; private set; }

    public IVideoOutput? Output
    {
        get
        {
            lock (_lock)
                return _output;
        }
    }

    public Controller(string host, int port, string clientName, Func<Action<string>, IVideoOutput> makeOutput, IControllerEvents events,
        IPairingStore pairings, Action<Action> post, bool allowPairing = false, IReadOnlyList<string>? transports = null)
    {
        _events = events;
        _makeOutput = makeOutput;
        _post = post;
        Session = new Session(host, port, clientName, this, pairings, allowPairing, transports);
    }

    public void Start() => Session.Start();

    /// <summary>Disconnect; OnEnded("closed") follows.</summary>
    public void Stop() => Session.Close();

    public void SetCamera(string facing) => Session.SetCamera(facing);

    // ISessionListener, called on the session threads.

    public void OnPending(string code) => _post(() => _events.OnPending(code));

    public void OnAccept(StreamInfo info)
    {
        Info = info;
        IVideoOutput output;
        try
        {
            output = _makeOutput(OnOutputError);
            output.Start(info);
        }
        catch (VideoException error)
        {
            Log.Error($"video output failed: {error.Message}");
            OutputError = error.Message;
            Session.Close();
            return;
        }
        lock (_lock)
        {
            output.SetRotation(_rotation);
            _output = output;
        }
        _post(() =>
        {
            if (!_ended)
                _events.OnStreaming(info);
        });
    }

    public void OnFrame(VideoFrame frame)
    {
        lock (_lock)
            _output?.Push(frame);
    }

    public void OnCamera(int rotation, string facing)
    {
        Log.Debug($"rotation {rotation}, {facing} camera");
        lock (_lock)
        {
            _rotation = rotation;
            _output?.SetRotation(rotation);
        }
        _post(() => _events.OnCamera(facing));
    }

    public void OnEnd(string reason) => _post(() => Finish(reason));

    void OnOutputError(string message)
    {
        Log.Error($"video output failed: {message}");
        OutputError = message;
        Session.Close();
    }

    void Finish(string reason)
    {
        if (_ended)
            return;
        _ended = true;
        IVideoOutput? output;
        lock (_lock)
        {
            output = _output;
            _output = null;
        }
        output?.Dispose();
        if (OutputError is not null && reason == EndReasons.Closed)
            reason = EndReasons.VideoError;
        Log.Info($"session ended: {reason}");
        _events.OnEnded(reason);
    }
}
