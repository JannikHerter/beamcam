namespace Beamcam;

/// <summary>The "Beamcam" camera other apps see. It exists while this object lives (Windows removes it
/// when the app closes), and shows the waiting picture until a phone streams.</summary>
public sealed class VirtualCamera : IDisposable
{
    public const int Width = 1280;
    public const int Height = 720;
    public const string Name = "Beamcam";

    internal IntPtr Handle { get; private set; }

    VirtualCamera(IntPtr handle) => Handle = handle;

    /// <summary>The camera, or null and why not, in words for the user.</summary>
    public static VirtualCamera? TryCreate(out string? problem)
    {
        problem = null;
        int hr;
        IntPtr handle;
        try
        {
            hr = Native.BcCameraCreate(out handle);
        }
        catch (DllNotFoundException)
        {
            problem = "Part of Beamcam is missing. Install Beamcam again.";
            return null;
        }
        if (hr >= 0)
            return new VirtualCamera(handle);
        Log.Error($"virtual camera failed: 0x{hr:x8}");
        problem = unchecked((uint)hr) switch
        {
            0x80040154 => "The Beamcam camera isn’t set up on this computer. Install Beamcam again.",
            0x8007007F => "Other apps can use Beamcam on Windows 11 only.",
            0x80070005 => "Windows didn’t let Beamcam add its camera. Make sure camera access is on in Settings → Privacy & security → Camera.",
            _ => $"The Beamcam camera couldn’t start (error 0x{hr:x8}). Restart your computer and try again.",
        };
        return null;
    }

    public void SetIdleFrame(ReadOnlySpan<byte> bgra, int stride) => Native.BcCameraSetIdleFrame(Handle, bgra, stride);

    public void ShowIdle() => Native.BcCameraShowIdle(Handle);

    /// <summary>How many apps' streams read the camera right now.</summary>
    public int Readers => Native.BcCameraClients(Handle);

    public void Dispose()
    {
        if (Handle == IntPtr.Zero)
            return;
        Native.BcCameraDestroy(Handle);
        Handle = IntPtr.Zero;
    }
}
