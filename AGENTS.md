# Beamcam

Beamcam turns an Android phone into a wireless webcam for Linux and Windows 11. The phone app streams
hardware-encoded H.264 over the local network; the computer app decodes it and feeds a virtual
camera that every video app sees as a normal camera (v4l2loopback on Linux, a Media Foundation
virtual camera on Windows).

## What we never compromise on

### 1. Obvious for non-technical people

Our users are not developers. Someone who has never opened a terminal must get from "installed
both apps" to "I'm on a video call" without reading docs: open both apps, click Connect, tap
Allow. Every screen says what to do next in plain words. Every error says what to try. If a
change adds a setting, a step or a term the user has to understand, find a way not to.

### 2. Private by default

The camera only turns on after the user allowed that computer on the phone, and the phone always
shows that it is streaming. Video never leaves the local network. No accounts, no cloud, no analytics.

### 3. Works on ordinary networks and distros

Home Wi-Fi, phone hotspots, Arch and Ubuntu alike. Do not depend on things that are often missing:
Avahi is off on many distros (we speak mDNS ourselves), firewalls block incoming connections on
the computer (the computer always dials the phone), and Google Play services may be absent.

### 4. Low latency

A webcam that lags behind the voice is broken. Prefer dropping frames over queueing them, on both sides.

## A small glossary

- **phone** means the Android device running `apps/android`. It is the server.
- **computer** means the Linux machine running `apps/linux` or the Windows 11 PC running `apps/windows`. It is the client.
- **virtual camera** means the camera other apps read from: the v4l2loopback device on Linux, the
  “Beamcam” Media Foundation virtual camera on Windows.
- **session** means one TCP connection from computer to phone, from HELLO to END.
- **allowed computer** means one the phone remembers (by the hash of its pairing secret); it does not ask again.
- **paired phone** means one whose certificate fingerprint the computer pinned; only those are auto-connected.

## Where code lives

- `apps/android/app/src/main/java/org/beamcam/`
  - `WebcamService.kt`: foreground service. Server socket, mDNS advertisement, sessions, approval.
  - `UdpLink.kt`: UDP video, resends and keyframe requests for one session. Send errors never end it.
  - `CameraStreamer.kt`: Camera2 into MediaCodec's input surface. No pixels pass through app code.
  - `Identity.kt`: the phone's TLS key in the Android keystore. `Secure.kt`: pairing code and UDP sealing.
  - `Protocol.kt`, `Orientation.kt`: wire format and rotation math (unit tested).
  - `MainActivity.kt`: Compose UI. It only reads `WebcamState` and sends intents to the service.
  - `AppLanguage.kt`: the language picked in the app; translations are in `res/values-*/strings.xml`.
- `fastlane/metadata/android/`: the phone app's F-Droid listing (texts, icon, changelogs), per language.
- `apps/linux/src/beamcam/`
  - `protocol.py` mirrors `Protocol.kt`, `secure.py` mirrors `Secure.kt`. `client.py` is one session
    (TLS control, sealed UDP video); `config.py` keeps the pairings.
    `reassembly.py` rebuilds frames from UDP datagrams and decides what to NACK. `mdns.py` finds phones.
  - `pipeline.py`: GStreamer decode, rotate, fixed-size output, v4l2sink. `controller.py` joins session and pipeline.
  - `reconnect.py` decides whether and where to reconnect (follows the phone's id through mDNS).
  - `i18n.py` and `locale/*.po`: translations for the window.
  - `ui.py` (GTK) and `cli.py` (scripts and tests) are thin layers over the controller. Only the
    window reconnects; the CLI exits so scripts see the failure.
  - `virtualcam.py` and `data/setup-virtual-camera` find and create the v4l2loopback device.
  - `packaging/`: Debian, Arch (also the AUR package) and Fedora packages, each built in a container
    by its `build-*.sh`. `install-data.sh` installs the menu entry, icon, app info and helper for
    all of them; a new file in `apps/linux/data` goes there too.
- `apps/windows/` (see its README for the picture):
  - `src/Beamcam.Core` (C#, any OS) ports the Linux core one to one: `Protocol.cs`, `Secure.cs`,
    `Session.cs`, `Reassembler.cs`, `Mdns.cs`, `Reconnector.cs`, `Config.cs`, `Controller.cs`.
    Change both computer apps together. `Text.cs` and `Strings*.resx`: translations for the window.
  - `src/Beamcam.Platform`: P/Invoke to the native DLL: `VirtualCamera.cs`, `NativeVideoOutput.cs` (decode thread).
  - `src/Beamcam` (WPF window) and `src/Beamcam.Cli` (`beamcam-cli.exe`) are thin layers over the controller.
  - `native/` (C++): `BeamcamCamera.dll`. `media_source.cpp` is the source Windows' Frame Server loads;
    `camera.cpp` creates the camera and serves it frames over a named pipe; `video.cpp` decodes with
    Media Foundation; `convert.cpp` rotates and fits (unit tested, no Windows dependency).
  - `installer/Beamcam.iss`: Inno Setup; registers the DLL and the firewall rule. `cross/`: Linux cross-build.
- `docs/internals/protocol.md` is the contract between the apps.

## Hit every surface

The most common defect is a change that works on the path you tested and is missing elsewhere.
Before calling work done, say which of these applied:

- **All apps.** Anything crossing the wire changes `Protocol.kt`, `protocol.py`, `Protocol.cs`, their
  test files and `docs/internals/protocol.md` together. Unknown message types must stay ignorable.
- **Both computers.** A change to sessions, pairing, reconnecting or the window's flow on Linux has a
  twin in `apps/windows`, and the other way round.
- **Both entry points.** The GUI (`ui.py`, `MainWindow.xaml.cs`) and the CLI (`cli.py`, `Beamcam.Cli`)
  share the controller; logic belongs there or below, not in one of them.
- **Phone UI and notification.** The phone may be locked or the app in the background. Anything the
  user must act on (approval, stop) works from the notification too.
- **Reverse states.** A way in needs a way out: allow has "Allowed computers" (remove one or all),
  pairing has "Paired Phones" (forget one or all), setting up the virtual camera has "Remove Virtual Camera"
  on Linux; on Windows the camera lives only while the app runs and the uninstaller removes the rest.
- **Orientation and both cameras.** Portrait, landscape, front and back all produce an upright picture.
- **Every language.** New or changed text needs its German version too; the tests catch gaps on the computer, lint on the phone.
- **Docs.** Check whether the change makes `docs/user` guidance wrong.

## Verifying

- Android unit tests and lint (lint fails on a missing translation): `cd apps/android && ./gradlew testDebugUnitTest lintDebug`
- Linux unit tests: `cd apps/linux && PYTHONPATH=src python3 -m unittest discover -s tests -t .`
- Windows unit tests: `cd apps/windows && dotnet test tests/Beamcam.Core.Tests` (runs on Linux too, see
  [development](docs/operations/development.md#windows)) and `native/tests/convert_tests.cpp`.
- CI (`.github/workflows/ci.yml`) runs all of the above on every push to main and every pull request; its
  Windows job also builds the native DLL with MSVC and the installer. The Linux tests run on x64 and arm64,
  and the `.deb` and `.rpm` are built and installed in clean containers on both, the Arch package on x64
  (`packaging/check-install.sh`).
- Windows end to end needs a Windows 11 machine or VM on the same network as a phone; the runbook has the steps.
- End to end on an emulator: `scripts/e2e-emulator.sh` (see [development](docs/operations/development.md#end-to-end-test)).
  It also exercises the virtual camera when a writable v4l2loopback device exists.
- Test behavior that can break: framing, mDNS parsing, rotation math, session state, pipeline output size.
  Skip tests for UI copy and wiring.
- The emulator cannot join your Wi-Fi's multicast, so phone discovery on a real network is only
  covered by `tests/test_mdns.py`. Say so when a change touches discovery.

## Taste

- Keep the two apps dumb about each other: the protocol doc is the only shared truth.
- Complexity belongs at the edges (camera, codec, sockets, kernel module). UI code only renders state.
- No new runtime dependencies on Linux beyond what distros ship (PyGObject, GTK 4, libadwaita,
  GStreamer, python-cryptography for AES-GCM). On Android, prefer the platform over libraries.
  On Windows, .NET and Windows itself (SChannel, CNG, Media Foundation) only; the installer bundles .NET.
- Never roll your own crypto. TLS comes from the platform (Conscrypt, Python `ssl`, .NET `SslStream`),
  AES-GCM from JCA, python-cryptography and .NET `AesGcm`; pairing rules live in `docs/internals/protocol.md`.
- Default to no comment. Write one only for a constraint, an external quirk or an invariant the code can't show.
- User-facing text is translated: `strings.xml` on the phone, `_()` on Linux, `Strings.resx` on
  Windows, and every language updated in the same change (see [translations](docs/operations/development.md#translations)).
- User-facing text is short, friendly and tells people what to do. Use “Allow”, not “Authorize”;
  “Wi-Fi”, not “LAN”. Don't name other products (say “your video call”, not a brand); code
  comments and internal docs may, where they record a real compatibility fact.
- If a rule here fights the task in front of you, say so and get a human sign-off before breaking it.

## Plans and work artifacts

Do not commit plans, research notes or scratch files. Never commit `local.properties`, keystores or signing keys.
