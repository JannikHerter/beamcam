namespace Beamcam;

/// <summary>Minimal logging: lines go to the sinks the app or command sets up (file, console).</summary>
public static class Log
{
    public static bool Verbose { get; set; }
    static readonly List<Action<string>> Sinks = [];
    static readonly Lock Guard = new();

    public static void AddSink(Action<string> sink)
    {
        lock (Guard)
            Sinks.Add(sink);
    }

    static void Write(string level, string message)
    {
        var line = $"{DateTime.Now:HH:mm:ss.fff} {level} {message}";
        lock (Guard)
            foreach (var sink in Sinks)
                sink(line);
    }

    public static void Debug(string message)
    {
        if (Verbose)
            Write("DEBUG", message);
    }

    public static void Info(string message) => Write("INFO", message);
    public static void Warning(string message) => Write("WARNING", message);
    public static void Error(string message) => Write("ERROR", message);
}
