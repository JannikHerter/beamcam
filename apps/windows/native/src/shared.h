// What the app and the virtual camera source (loaded by the Windows Frame Server) agree on.
#pragma once

#include <windows.h>

#include <cstdint>
#include <cstdio>

namespace bc {

// {278cd7d4-90ab-466e-9a40-495097d2142c}
inline constexpr GUID kSourceClsid = {0x278cd7d4, 0x90ab, 0x466e, {0x9a, 0x40, 0x49, 0x50, 0x97, 0xd2, 0x14, 0x2c}};
inline constexpr wchar_t kSourceClsidString[] = L"{278cd7d4-90ab-466e-9a40-495097d2142c}";
inline constexpr wchar_t kCameraName[] = L"Beamcam";

// The app serves decoded frames on this pipe; the source in the Frame Server reads them.
inline constexpr wchar_t kPipeName[] = L"\\\\.\\pipe\\Beamcam.VirtualCamera";
inline constexpr int kCameraWidth = 1280;
inline constexpr int kCameraHeight = 720;
inline constexpr int kCameraFps = 30;
inline constexpr uint32_t kFrameBytes = kCameraWidth * kCameraHeight * 3 / 2;
inline constexpr uint32_t kFrameMagic = 0x31464342;  // "BCF1"

// Every frame on the pipe: this header, then `size` bytes of NV12 (Y plane, then interleaved UV).
struct FrameHeader {
    uint32_t magic;
    uint32_t width;
    uint32_t height;
    uint32_t size;
};

// Off unless HKLM\SOFTWARE\Beamcam CameraLog = 1: writes to %TEMP%\beamcam-camera.log of whichever
// process loaded the source (the Frame Server's is under C:\Windows\ServiceProfiles\LocalService).
void Trace(const char* format, ...);

}  // namespace bc
