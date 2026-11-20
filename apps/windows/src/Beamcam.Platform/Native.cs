using System.Runtime.InteropServices;

namespace Beamcam;

/// <summary>native/src/beamcam.h</summary>
static partial class Native
{
    const string Dll = "BeamcamCamera.dll";

    [LibraryImport(Dll)] internal static partial int BcCameraCreate(out IntPtr camera);
    [LibraryImport(Dll)] internal static partial void BcCameraSetIdleFrame(IntPtr camera, ReadOnlySpan<byte> bgra, int stride);
    [LibraryImport(Dll)] internal static partial void BcCameraShowIdle(IntPtr camera);
    [LibraryImport(Dll)] internal static partial int BcCameraClients(IntPtr camera);
    [LibraryImport(Dll)] internal static partial void BcCameraDestroy(IntPtr camera);

    [LibraryImport(Dll)] internal static partial int BcVideoCreate(int width, int height, int fps, IntPtr camera, out IntPtr video);
    [LibraryImport(Dll)] internal static partial int BcVideoPush(IntPtr video, ReadOnlySpan<byte> data, int size, long ptsUs, int rotation);
    [LibraryImport(Dll)] internal static partial long BcVideoFrames(IntPtr video);
    [LibraryImport(Dll)] internal static partial long BcVideoCopyPreview(IntPtr video, long seen, IntPtr bgra, int stride);
    [LibraryImport(Dll)] internal static partial int BcVideoSnapshot(IntPtr video, Span<byte> bgra, int stride);
    [LibraryImport(Dll)] internal static partial void BcVideoDestroy(IntPtr video);
}
