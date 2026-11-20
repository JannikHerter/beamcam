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
            problem = Text.Get(MissingMedia() ? "CameraMediaFeaturePack" : "CameraMissingPart");
            return null;
        }
        if (hr >= 0)
            return new VirtualCamera(handle);
        Log.Error($"virtual camera failed: 0x{hr:x8}");
        problem = unchecked((uint)hr) switch
        {
            0x80040154 => Text.Get("CameraNotRegistered"),
            0x8007007F => Text.Get("CameraNeedsWindows11"),
            0x80070005 => Text.Get("CameraAccessDenied"),
            _ => Text.Format("CameraFailed", $"0x{hr:x8}"),
        };
        return null;
    }

    /// <summary>"N" editions of Windows come without Media Foundation.</summary>
    internal static bool MissingMedia()
    {
        if (!System.Runtime.InteropServices.NativeLibrary.TryLoad("mfplat.dll", out var library))
            return true;
        System.Runtime.InteropServices.NativeLibrary.Free(library);
        return false;
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
