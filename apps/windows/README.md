# Beamcam for Windows

The computer side of [Beamcam](../../README.md) for **Windows 11**: finds your phone, shows the
picture and feeds the “Beamcam” camera that every app on the computer can choose. Same protocol,
pairing and reconnect rules as the [Linux app](../linux/README.md); the
[protocol](../../docs/internals/protocol.md) is the only thing the apps share.

## How it fits together

```
 phone ──TLS + UDP──▶ Beamcam.exe ──────────────────────────────▶ window preview
                     (C#: Core session, Platform decode)
                          │ upright 1280x720 NV12, named pipe
                          ▼
              Frame Server (Windows' camera service) loads BeamcamCamera.dll
                          │
                          ▼
              any app that opens the camera “Beamcam”
```

- `src/Beamcam.Core` (C#, any OS): protocol, sealing, UDP reassembly and NACKs, mDNS, session,
  reconnect rules, pairings. A port of `apps/linux/src/beamcam`; the tests mirror the Linux ones.
- `src/Beamcam.Platform` (C#, Windows): the virtual camera and the decoder, through `Native.cs`.
- `src/Beamcam` (C#, WPF with the Windows 11 look): the window. Only renders state.
- `src/Beamcam.Cli`: `beamcam-cli.exe` for scripts and tests (`discover`, `stream`).
- `native/` (C++): `BeamcamCamera.dll`. Two halves in one file:
  - the **media source** the Frame Server loads when an app opens the camera
    (`media_source.cpp`, `dllmain.cpp`). It reads frames from the pipe `\\.\pipe\Beamcam.VirtualCamera`
    and shows black without the app;
  - what the app calls: `MFCreateVirtualCamera` and the pipe server (`camera.cpp`), Windows'
    H.264 decoder (`video.cpp`) and the rotate-and-fit math (`convert.cpp`, unit tested).
- `installer/Beamcam.iss`: the Inno Setup installer.

Why this split: a virtual camera on Windows 11 is a COM media source that the Frame Server
(a service in session 0) loads, so that part has to be a native DLL. Everything else is C#:
TLS (`SslStream`, Windows' own TLS), AES-GCM (`AesGcm`, Windows' CNG), sockets and JSON are in
.NET itself, WPF gives an accessible native-looking window, and the core is testable on Linux too.

The camera exists while Beamcam runs (`MFVirtualCameraLifetime_Session`): open Beamcam before you
pick the camera in another app. Closing Beamcam removes it; uninstalling removes everything else.
No setup step and no “remove camera” menu are needed.

## Build

Everything except the installer builds on Linux in containers:

```sh
apps/windows/cross/build.sh          # -> apps/windows/out/app (the app) and out/tools
```

On Windows with Visual Studio 2022 (C++ desktop workload) and the .NET 10 SDK:

```powershell
cmake -S native -B out\native -A x64; cmake --build out\native --config Release
dotnet publish src\Beamcam\Beamcam.csproj -c Release -r win-x64 --self-contained -o out\app
dotnet publish src\Beamcam.Cli\Beamcam.Cli.csproj -c Release -r win-x64 --self-contained -o out\app
copy out\native\Release\BeamcamCamera.dll out\app\
iscc installer\Beamcam.iss          # -> out\Beamcam-Setup-0.1.0.exe (Inno Setup 6)
```

## Test

```sh
# Linux or Windows: the C# core (protocol, sealing, reassembly, mDNS, sessions, pairings)
dotnet test tests/Beamcam.Core.Tests
# Rotation and scaling (plain C++; on Windows it is out/tools/convert_tests.exe)
g++ -std=c++20 -o /tmp/convert_tests native/tests/convert_tests.cpp native/src/convert.cpp && /tmp/convert_tests
```

In Windows, after installing:

```powershell
& "C:\Program Files\Beamcam\beamcam-cli.exe" discover
& "C:\Program Files\Beamcam\beamcam-cli.exe" stream 192.168.1.23 --frames 300 --snapshot frame.png
out\tools\beamcam-capture.exe --frames 90 --out camera.bmp   # reads "Beamcam" like a video app
ffmpeg -f dshow -list_devices true -i dummy                    # also lists "Beamcam"
```

More in the [development runbook](../../docs/operations/development.md#windows).

## Troubleshooting the camera

- The Frame Server loads `BeamcamCamera.dll` as LOCAL SERVICE, so it must live in a folder that
  account can read (Program Files is fine; a user's Downloads folder is not).
- Set `HKLM\SOFTWARE\Beamcam` `CameraLog` (DWORD) to `1` to log from the source: each process that
  loads it writes `%TEMP%\beamcam-camera.log`; the Frame Server's is under
  `C:\Windows\ServiceProfiles\LocalService\AppData\Local\Temp`.
- The app logs to `%LOCALAPPDATA%\Beamcam\beamcam.log` (`Beamcam.exe --verbose` for more).
