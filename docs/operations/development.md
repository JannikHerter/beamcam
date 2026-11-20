# Development

## Requirements

- **Android app:** Android SDK (with platform 37 and build-tools 36; Gradle downloads missing ones
  once licenses are accepted) and a JDK 17–27. The Gradle wrapper (9.9) is checked in.
- **Linux app:** Python 3.10+, PyGObject, python-cryptography, GTK 4, libadwaita 1.5+, GStreamer 1.22+ with
  gst-plugins-base, -good (`v4l2sink`, `videoflip`), gst-libav (`avdec_h264`) and gst-plugin-gtk4
  (`gtk4paintablesink`). On Arch: `pacman -S python-gobject python-cryptography gtk4 libadwaita gst-plugins-good gst-libav gst-plugin-gtk4`.
- **Virtual camera:** `v4l2loopback-dkms` (see `install_command()` in `apps/linux/src/beamcam/virtualcam.py`).

## Build and run

```sh
# Android
cd apps/android
./gradlew assembleDebug testDebugUnitTest
adb install -r -g app/build/outputs/apk/debug/app-debug.apk

# Linux
cd apps/linux
PYTHONPATH=src python3 -m beamcam               # the app
PYTHONPATH=src python3 -m beamcam discover      # list phones
PYTHONPATH=src python3 -m beamcam -v stream 192.168.1.23 --no-camera --frames 300
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

`pipx install --system-site-packages ./apps/linux` installs a `beamcam` command instead
(`--system-site-packages` lets it use the distribution's PyGObject and GStreamer). It adds no
menu entry or icon; only the packages below do.

## Packages

What users install. Both put the app in the app menu with its icon, and install the virtual
camera helper with a polkit action, so the password prompt says what it is for.
`apps/linux/packaging/install-data.sh` lists those files once for every package.

```sh
# Arch, Manjaro: build and install the checkout
cd apps/linux/packaging/arch && makepkg -si
# Debian 13+, Ubuntu 26.04+: builds in a container, writes apps/linux/packaging/dist/*.deb
apps/linux/packaging/build-deb.sh
sudo apt install ./apps/linux/packaging/dist/beamcam_*.deb
```

Older releases such as Ubuntu 24.04 lack `gstreamer1.0-gtk4`, which the preview needs.
`makepkg` and `dpkg-buildpackage` leave `build/` in `apps/linux`; it is ignored by git.

## Translations

Both apps follow the device's language and offer **Language** in their menu to pick another.
English is the source; German is the only translation so far.

- **Phone:** `res/values/strings.xml`, with one `values-<lang>/strings.xml` per language. A new
  language also goes in `res/xml/locales_config.xml` and `AppLanguage.SUPPORTED`. Android 13+
  keeps the choice itself (it also appears in the phone's Settings → Apps → Beamcam → Language);
  older versions store it in the app's settings and apply it in `AppLanguage.wrap`.
- **Computer:** texts are wrapped in `_()` (or `N_()` where they are defined and shown elsewhere).
  Translations are gettext `.po` files in `apps/linux/src/beamcam/locale/`, edited with any .po
  editor and read at startup, so no compile step is needed. A new language also goes in
  `i18n.LANGUAGES`. `tests/test_i18n.py` fails when a text is untranslated, no longer used, or its
  `{placeholders}` differ. Picking a language restarts the window. The command line stays
  English, because scripts read it.

## Emulator

The emulator sits behind its own NAT: its mDNS may reach the computer, but its address (10.0.2.x)
is not reachable. Forward the port and connect to localhost:

```sh
adb forward tcp:47800 tcp:47800
PYTHONPATH=src python3 -m beamcam stream 127.0.0.1:47800 --no-camera --frames 150 --snapshot /tmp/frame.png
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

- `adb install -r -g …` and open the app; then `beamcam discover` should list the phone and
  `beamcam stream <phone-ip> --frames 300 --snapshot /tmp/f.png` streams over Wi-Fi.
- For a real camera consumer without clicking through a browser, run Firefox headless with a
  throwaway profile whose `user.js` sets `permissions.default.camera` to `1` and
  `media.navigator.permission.disabled` to `true`, and open a page that calls `getUserMedia` and
  counts frames with `requestVideoFrameCallback`. (T3 Code's built-in browser denies camera access.)
- Reference numbers (Redmi Note 10 Pro, 2.4 GHz Wi-Fi): 29.6 fps in Firefox, phone sensor to encoded
  frame about 71 ms, computer receive to virtual camera about 2.5 ms.

## Virtual camera by hand

```sh
sudo sh apps/linux/src/beamcam/data/setup-virtual-camera            # create + persist
sudo sh apps/linux/src/beamcam/data/setup-virtual-camera --remove   # undo
v4l2-ctl --list-devices
```
