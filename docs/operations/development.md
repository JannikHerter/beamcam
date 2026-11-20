# Development

## Requirements

- **Android app:** Android SDK (with platform 37 and build-tools 36; Gradle downloads missing ones
  once licenses are accepted) and a JDK 17–27. The Gradle wrapper (9.9) is checked in.
- **Linux app:** Python 3.10+, PyGObject, python-cryptography, GTK 4, libadwaita 1.5+, GStreamer 1.22+ with
  gst-plugins-base, -good (`v4l2sink`, `videoflip`), gst-libav (`avdec_h264`) and gst-plugin-gtk4
  (`gtk4paintablesink`). On Arch: `pacman -S python-gobject python-cryptography gtk4 libadwaita gst-plugins-good gst-libav gst-plugin-gtk4`.
- **Virtual camera:** `v4l2loopback-dkms` (see `install_command()` in `apps/linux/src/openwebcam/virtualcam.py`).

## Build and run

```sh
# Android
cd apps/android
./gradlew assembleDebug testDebugUnitTest
adb install -r -g app/build/outputs/apk/debug/app-debug.apk

# Linux
cd apps/linux
PYTHONPATH=src python3 -m openwebcam               # the app
PYTHONPATH=src python3 -m openwebcam discover      # list phones
PYTHONPATH=src python3 -m openwebcam -v stream 192.168.1.23 --no-camera --frames 300
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

`pipx install --system-site-packages ./apps/linux` installs an `open-webcam` command instead
(`--system-site-packages` lets it use the distribution's PyGObject and GStreamer).

## Emulator

The emulator sits behind its own NAT: its mDNS may reach the computer, but its address (10.0.2.x)
is not reachable. Forward the port and connect to localhost:

```sh
adb forward tcp:47800 tcp:47800
PYTHONPATH=src python3 -m openwebcam stream 127.0.0.1:47800 --no-camera --frames 150 --snapshot /tmp/frame.png
```

Start it with `-camera-front emulated -camera-back emulated` for cameras that work without a GPU.
`-read-only` keeps the AVD unchanged.

## End-to-end test

`scripts/e2e-emulator.sh` builds and installs the app on the running emulator, streams, switches
cameras and, when a writable v4l2loopback device exists, writes to it and reads it back with
GStreamer and ffmpeg like a video app would. Pass `--launch` to start the app with `adb`; otherwise
open it yourself after the install step. The first run needs a tap on **Allow** in the emulator.

adb forwards only TCP, so on the emulator video always takes the TCP fallback ("Video transport:
tcp"); the UDP path needs a real phone on the same network. With several devices attached, set
`ANDROID_SERIAL=emulator-5554`.

What it cannot cover: discovery over a real Wi-Fi network, a real phone's encoder, UDP video, and
the `pkexec` prompt. Check those by hand before a release.

## Real phone and a real browser

- `adb install -r -g …` and open the app; then `open-webcam discover` should list the phone and
  `open-webcam stream <phone-ip> --frames 300 --snapshot /tmp/f.png` streams over Wi-Fi.
- For a real camera consumer without clicking through a browser, run Firefox headless with a
  throwaway profile whose `user.js` sets `permissions.default.camera` to `1` and
  `media.navigator.permission.disabled` to `true`, and open a page that calls `getUserMedia` and
  counts frames with `requestVideoFrameCallback`. (T3 Code's built-in browser denies camera access.)
- Reference numbers (Redmi Note 10 Pro, 2.4 GHz Wi-Fi): 29.6 fps in Firefox, phone sensor to encoded
  frame about 71 ms, computer receive to virtual camera about 2.5 ms.

## Virtual camera by hand

```sh
sudo sh apps/linux/src/openwebcam/data/setup-virtual-camera            # create + persist
sudo sh apps/linux/src/openwebcam/data/setup-virtual-camera --remove   # undo
v4l2-ctl --list-devices
```
