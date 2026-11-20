using System.IO;
using System.Threading;
using System.Windows;

namespace Beamcam.App;

public partial class App : Application
{
    const string InstanceName = "Local\\Beamcam.Window";
    Mutex? _instance;

    protected override void OnStartup(StartupEventArgs e)
    {
        // One window per user: a second start brings the first one to the front.
        var show = new EventWaitHandle(false, EventResetMode.AutoReset, InstanceName + ".Show", out _);
        _instance = new Mutex(true, InstanceName, out var first);
        if (!first)
        {
            show.Set();
            Shutdown();
            return;
        }
        Log.Verbose = e.Args.Contains("--verbose");
        StartLog();
        Log.Info($"language {Text.Apply(Config.Load().Language)}");
        base.OnStartup(e);
        var window = new MainWindow();
        window.Show();
        new Thread(() =>
        {
            while (show.WaitOne())
                Dispatcher.BeginInvoke(window.BringToFront);
        }) { IsBackground = true, Name = "show" }.Start();
    }

    /// <summary>Starts a new window process (so every text uses the new language) and ends this one.</summary>
    public bool Restart()
    {
        var path = Environment.ProcessPath;
        if (path is null)
            return false;
        MainWindow?.Close();
        _instance?.ReleaseMutex();
        _instance?.Dispose();
        _instance = null;
        try
        {
            System.Diagnostics.Process.Start(path);
        }
        catch (System.ComponentModel.Win32Exception error)
        {
            Log.Error($"restart failed: {error.Message}");
        }
        Shutdown();
        return true;
    }

    static void StartLog()
    {
        try
        {
            var path = Path.Combine(Path.GetDirectoryName(Config.DefaultPath())!, "beamcam.log");
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            if (File.Exists(path) && new FileInfo(path).Length > 1 << 20)
                File.Move(path, path + ".old", overwrite: true);
            var writer = new StreamWriter(path, append: true) { AutoFlush = true };
            Log.AddSink(writer.WriteLine);
            Log.Info($"Beamcam {typeof(App).Assembly.GetName().Version} on Windows {Environment.OSVersion.Version}");
        }
        catch (IOException)
        {
        }
    }
}
