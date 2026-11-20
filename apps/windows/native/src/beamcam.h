// C functions the Beamcam app calls (P/Invoke, see Beamcam.Platform/Native.cs).
#pragma once

#include <windows.h>

#include <cstdint>

extern "C" {

struct BcCamera;
struct BcVideo;

// Creates the "Beamcam" virtual camera for as long as the app runs, and serves it frames.
// Fails when the source is not registered (REGDB_E_CLASSNOTREG) or before Windows 11.
HRESULT __stdcall BcCameraCreate(BcCamera** camera);
// The picture other apps see while no phone streams: 1280x720 BGRA.
void __stdcall BcCameraSetIdleFrame(BcCamera* camera, const uint8_t* bgra, int stride);
void __stdcall BcCameraShowIdle(BcCamera* camera);
// How many readers (Frame Server streams) are connected right now.
int __stdcall BcCameraClients(BcCamera* camera);
void __stdcall BcCameraDestroy(BcCamera* camera);

// An H.264 decoder whose output, turned upright and fit into 1280x720, goes to `camera` (may be null)
// and to the preview.
HRESULT __stdcall BcVideoCreate(int width, int height, int fps, BcCamera* camera, BcVideo** video);
// One Annex-B access unit. rotation: clockwise degrees that make the picture upright.
HRESULT __stdcall BcVideoPush(BcVideo* video, const uint8_t* data, int size, int64_t ptsUs, int rotation);
int64_t __stdcall BcVideoFrames(BcVideo* video);
// Copies the newest preview (640x360 BGRA) if it is newer than `seen`; returns its number.
int64_t __stdcall BcVideoCopyPreview(BcVideo* video, int64_t seen, uint8_t* bgra, int stride);
// The newest output frame as 1280x720 BGRA. S_FALSE if there is none yet.
HRESULT __stdcall BcVideoSnapshot(BcVideo* video, uint8_t* bgra, int stride);
void __stdcall BcVideoDestroy(BcVideo* video);
}
