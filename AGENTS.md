# Open Webcam

Open Webcam turns an Android phone into a wireless webcam for Linux. The phone app streams
hardware-encoded H.264 over the local network; the Linux app decodes it and writes it to a
v4l2loopback device that every video app sees as a normal camera.

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
- **computer** means the Linux machine running `apps/linux`. It is the client.
- **virtual camera** means the v4l2loopback device other apps read from.
- **session** means one TCP connection from computer to phone, from HELLO to END.
- **allowed computer** means one the phone remembers; it does not ask again.

## Where code lives

- `apps/android/app/src/main/java/org/openwebcam/`
  - `WebcamService.kt`: foreground service. Server socket, mDNS advertisement, sessions, approval.
  - `CameraStreamer.kt`: Camera2 into MediaCodec's input surface. No pixels pass through app code.
  - `Protocol.kt`, `Orientation.kt`: wire format and rotation math (unit tested).
  - `MainActivity.kt`: Compose UI. It only reads `WebcamState` and sends intents to the service.
- `apps/linux/src/openwebcam/`
  - `protocol.py` mirrors `Protocol.kt`. `client.py` is one session. `mdns.py` finds phones.
  - `pipeline.py`: GStreamer decode, rotate, fixed-size output, v4l2sink. `controller.py` joins session and pipeline.
  - `ui.py` (GTK) and `cli.py` (scripts and tests) are thin layers over the controller.
  - `virtualcam.py` and `data/setup-virtual-camera` find and create the v4l2loopback device.
- `docs/internals/protocol.md` is the contract between the two apps.

## Hit every surface

The most common defect is a change that works on the path you tested and is missing elsewhere.
Before calling work done, say which of these applied:

- **Both apps.** Anything crossing the wire changes `Protocol.kt`, `protocol.py`, both test files
  and `docs/internals/protocol.md` together. Unknown message types must stay ignorable.
- **Both entry points on Linux.** The GUI (`ui.py`) and the CLI (`cli.py`) share `controller.py`;
  logic belongs there or below, not in one of them.
- **Phone UI and notification.** The phone may be locked or the app in the background. Anything the
  user must act on (approval, stop) works from the notification too.
- **Reverse states.** A way in needs a way out: allow has "Forget allowed computers", setting up
  the virtual camera has "Remove Virtual Camera".
- **Orientation and both cameras.** Portrait, landscape, front and back all produce an upright picture.
- **Docs.** Check whether the change makes `docs/user` guidance wrong.

## Verifying

- Android unit tests: `cd apps/android && ./gradlew testDebugUnitTest`
- Linux unit tests: `cd apps/linux && PYTHONPATH=src python3 -m unittest discover -s tests -t .`
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
  GStreamer). On Android, prefer the platform over libraries.
- Default to no comment. Write one only for a constraint, an external quirk or an invariant the code can't show.
- User-facing text is short, friendly and tells people what to do. Use “Allow”, not “Authorize”;
  “Wi-Fi”, not “LAN”.
- If a rule here fights the task in front of you, say so and get a human sign-off before breaking it.

## Plans and work artifacts

Do not commit plans, research notes or scratch files. Never commit `local.properties`, keystores or signing keys.
