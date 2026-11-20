<img src="assets/icon.svg" width="64" height="64" alt="">

# Open Webcam

Use your Android phone as a wireless webcam on Linux, for video calls, your browser and any other app that uses a camera.

1. Install **Open Webcam** on your phone and on your computer.
2. Open both. Your phone shows up on the computer; click **Connect**.
3. Tap **Allow** on the phone the first time.
4. In your video call, browser or any other app, pick the camera **Open Webcam**.

The video goes straight from your phone to your computer over your Wi-Fi. Nothing is uploaded to the internet.

- [Getting started](docs/user/getting-started.md)
- [Troubleshooting](docs/user/troubleshooting.md)
- [All docs](docs/README.md)

## Status

Early development (0.1). Not released yet: build it from source with the
[development runbook](docs/operations/development.md).

## Repository

- `apps/android`: the phone app (Kotlin, Jetpack Compose, Camera2, MediaCodec)
- `apps/linux`: the computer app (Python, GTK 4 + libadwaita, GStreamer, v4l2loopback)
- `docs/`: user guides, internals and runbooks
- `scripts/`: end-to-end test against an Android emulator
