# Development

## Requirements

- **Android app:** Android SDK (with platform 37 and build-tools 36; Gradle downloads missing ones
  once licenses are accepted) and a JDK 17–27. The Gradle wrapper (9.9) is checked in.
- **Linux app:** Python 3.10+, PyGObject, python-cryptography, GTK 4, libadwaita 1.5+, GStreamer 1.22+ with
  gst-plugins-base, -good (`v4l2sink`, `videoflip`), gst-libav (`avdec_h264`) and gst-plugin-gtk4
  (`gtk4paintablesink`). On Arch: `pacman -S python-gobject python-cryptography gtk4 libadwaita gst-plugins-good gst-libav gst-plugin-gtk4`.
- **Virtual camera:** `v4l2loopback-dkms` (see `install_command()` in `apps/linux/src/beamcam/virtualcam.py`).
- **Windows app:** builds on Linux with Docker only (see [Windows](#windows)); on Windows, the .NET 10
  SDK, Visual Studio 2022 with the C++ desktop workload (or its Build Tools) and Inno Setup 6.

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

All apps follow the device's language and offer **Language** in their menu to pick another.
English is the source; German is the only translation so far.

- **Phone:** `res/values/strings.xml`, with one `values-<lang>/strings.xml` per language. A new
  language also goes in `res/xml/locales_config.xml` and `AppLanguage.SUPPORTED`. Android 13+
  keeps the choice itself (it also appears in the phone's Settings → Apps → Beamcam → Language);
  older versions store it in the app's settings and apply it in `AppLanguage.wrap`.
- **Linux:** texts are wrapped in `_()` (or `N_()` where they are defined and shown elsewhere).
  Translations are gettext `.po` files in `apps/linux/src/beamcam/locale/`, edited with any .po
  editor and read at startup, so no compile step is needed. A new language also goes in
  `i18n.LANGUAGES`. `tests/test_i18n.py` fails when a text is untranslated, no longer used, or its
  `{placeholders}` differ. Picking a language restarts the window. The command line stays
  English, because scripts read it.
- **Windows:** texts are keys in `apps/windows/src/Beamcam.Core/Strings.resx`, with one
  `Strings.<lang>.resx` per language, read through `Text.Get` and `{app:Tr Key}` in XAML. Keep the
  wording of Linux's `.po` where the texts match. A new language also goes in `Text.Languages` and the
  installer's `[Languages]`. `TextTests` fails when a text is untranslated, a key is missing or
  unused, or its placeholders differ. Picking a language restarts the window; `beamcam-cli` stays English.

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

## Windows

The Windows 11 app lives in `apps/windows` (layout in its [README](../../apps/windows/README.md)).

### Build and unit tests

```sh
cd apps/windows
cross/build.sh     # Linux, in containers: out/app (the app) and out/tools (test programs)
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -v "$PWD":/src -w /src mcr.microsoft.com/dotnet/sdk:10.0 \
  dotnet test tests/Beamcam.Core.Tests
```

`cross/build.sh` compiles `BeamcamCamera.dll` with clang-cl against Microsoft's CRT and SDK (fetched
by [xwin](https://github.com/Jake-Shadle/xwin), which accepts Microsoft's license for them) and
publishes the C# projects self-contained for `win-x64`. The installer is made in Windows:
`iscc installer\Beamcam.iss` with `out\app` next to it, giving `out\Beamcam-Setup-0.1.0.exe`.
On a Windows machine, the commands in the app's README build everything natively.

### A Windows 11 VM for end-to-end tests

Real phones need a Windows that sits on the same network, so mDNS and UDP from the phone reach it.
[dockurr/windows](https://github.com/dockur/windows) runs Windows 11 in Docker with KVM and downloads
it from Microsoft. With `DHCP=Y` and a macvlan network on a **wired** interface the VM gets its own
address from the router. The host itself cannot talk to a macvlan guest; a second container on the
same macvlan network can (a small container with `openssh-client`, below).

`DHCP=Y` needs the host's `macvtap` and `tun` kernel modules. They can't load when the kernel was
updated and the computer not restarted yet, so restart first. Give the VM's container a fixed
`--mac-address` so the router keeps giving it the same address.

```sh
docker network create -d macvlan --subnet=192.168.2.0/24 --gateway=192.168.2.1 \
  --ip-range=192.168.2.240/29 -o parent=<wired interface> beamcam-lan
docker run -d --name beamcam-win11 --network beamcam-lan --ip 192.168.2.241 \
  -e VERSION=11 -e RAM_SIZE=8G -e CPU_CORES=6 -e DISK_SIZE=64G -e DHCP=Y \
  -e USERNAME=tester -e PASSWORD=<password> \
  --device /dev/kvm --device /dev/net/tun --device /dev/vhost-net --device-cgroup-rule='c *:* rwm' \
  --cap-add NET_ADMIN -v beamcam-win11:/storage -v <oem folder>:/oem --stop-timeout 120 dockurr/windows
```

The OEM folder's `install.bat` runs once after setup; ours installs Win32-OpenSSH from its MSI, puts a
public key in `C:\ProgramData\ssh\administrators_authorized_keys` and turns off sleep. Then, from a
small container with `openssh-client` on `beamcam-lan`: `ssh tester@<vm address>` (find it with
`nmap -sn` or the router).

Programs started over SSH run in session 0 and cannot show windows. To run the app on the desktop,
start it with a scheduled task for the signed-in user (`schtasks /create /tn beamcam /tr ... /sc once
/st 00:00 /ru tester /it`, then `schtasks /run /tn beamcam`), and drive it with UI Automation from the
same session. Screenshots come from the same trick or from QEMU's monitor.

### End to end in Windows

1. Install `out\Beamcam-Setup-0.1.0.exe` (silent: `/VERYSILENT /SUPPRESSMSGBOXES`).
2. `beamcam-cli discover` lists the phone; `beamcam-cli stream <phone> --frames 300 --snapshot f.png`
   pairs on first use (tap **Allow**), `--tcp-only` forces the TCP fallback.
3. While the app or `beamcam-cli stream` runs, `beamcam-capture --frames 90 --out cam.bmp` reads the
   camera through Media Foundation like a browser does, and
   `ffmpeg -f dshow -i video="Beamcam (Windows Virtual Camera)" -frames:v 1 cam.png` reads it like a
   DirectShow app. Windows adds that suffix to the name of every virtual camera.
4. Compare orientation with the Linux CLI's `--snapshot` for the same phone position: both apps apply
   the same rotation.

Remove the VM with `docker rm -f beamcam-win11 && docker volume rm beamcam-win11 && docker network rm
beamcam-lan` (and `docker rmi dockurr/windows`).

## Releases

Pushing a tag `vX.Y.Z` runs `.github/workflows/release.yml`: it checks that every file says that
version, runs CI, builds the signed phone app, both Linux packages and the Windows installer, and
publishes them with `SHA256SUMS.txt` as a GitHub release. Versions starting with `0.` are marked
pre-release.

1. Set the new version everywhere `scripts/check-version.sh` looks, and raise `versionCode` in
   `apps/android/app/build.gradle.kts` by one (Android refuses an update with the same code).
   Add a section to `CHANGELOG.md` (it becomes the release notes), an entry on top of
   `apps/linux/packaging/debian/changelog` and a `<release>` in the metainfo.
2. `scripts/check-version.sh X.Y.Z`, then commit and push.
3. `git tag vX.Y.Z && git push origin vX.Y.Z`, and watch the Release workflow.

The phone app is signed with the release key. Phones only accept an update signed with the same
key, so losing it means every user has to uninstall and set Beamcam up again. The maintainer keeps
the key (`beamcam-release.p12`, alias `beamcam`) and its password backed up outside the repo; the
workflow gets them from the repository secrets `BEAMCAM_KEYSTORE_BASE64` and
`BEAMCAM_KEYSTORE_PASSWORD`, and refuses any certificate but the one pinned in the workflow.
To sign a release build locally, set `BEAMCAM_KEYSTORE` to the key file and
`BEAMCAM_KEYSTORE_PASSWORD`. Without them `assembleRelease` signs with the debug key, which is
handy for testing the shrunk release build over a debug install.
