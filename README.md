<img src="assets/icon.svg" width="64" height="64" alt="">

# Beamcam

Use your Android phone as a wireless webcam on Linux or Windows 11, for video calls, your browser and any other app that uses a camera.

1. [Download](https://github.com/JannikHerter/beamcam/releases/latest) and install **Beamcam** on your phone and on your computer.
2. Open both. Your phone shows up on the computer; click **Connect**.
3. Tap **Allow** on the phone the first time.
4. In your video call, browser or any other app, pick the camera **Beamcam**.

The video goes straight from your phone to your computer over your Wi-Fi. Nothing is uploaded to the internet.

- [Getting started](docs/user/getting-started.md)
- [Troubleshooting](docs/user/troubleshooting.md)
- [All docs](docs/README.md)

## Status

Early development. [Download the newest release](https://github.com/JannikHerter/beamcam/releases/latest) for your
phone and your computer; [Getting started](docs/user/getting-started.md#install) says which file you need.
To build it yourself, see the [development runbook](docs/operations/development.md).

## Repository

- `apps/android`: the phone app (Kotlin, Jetpack Compose, Camera2, MediaCodec)
- `apps/linux`: the computer app for Linux (Python, GTK 4 + libadwaita, GStreamer, v4l2loopback)
- `apps/windows`: the computer app for Windows 11 (C# and WPF, a C++ virtual camera for Media Foundation)
- `docs/`: user guides, internals and runbooks
- `scripts/`: end-to-end test against an Android emulator

## License

Beamcam is free software under the [GNU General Public License, version 3 or later](LICENSE).
