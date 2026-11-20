# Architecture overview

```
 Phone (server)                                        Computer (client)
 Camera2 ──surface──▶ MediaCodec H.264 ──TCP 47800──▶ GStreamer decode ─▶ rotate/scale ─▶ v4l2sink ─▶ /dev/videoN
                                     ◀─ mDNS query ──  mdns.py                           └▶ preview in the window
```

## Why the phone is the server

The computer dials the phone. Desktop firewalls (firewalld, ufw) block incoming connections by
default, but allow outgoing ones; Android has no inbound firewall. The phone only needs to accept
one connection at a time and asks the user before the first one from each computer.

## Discovery

The phone advertises `_beamcam._tcp` with Android's `NsdManager` (TXT `id` is a stable random
phone id, `v` the protocol version). The computer does **not** use Avahi: it is disabled by
default on Arch and Manjaro, and the D-Bus API adds a dependency. `mdns.py` binds UDP 5353 with
`SO_REUSEPORT` (so it coexists with Avahi when present), joins the group on every IPv4 interface
(VPNs often own the default route) and asks every few seconds. A phone that stops answering
disappears after `STALE_AFTER`; we do not rely on record TTLs, which are an hour long.

The address shown on the phone and **Connect by Address…** are the fallback for networks that
drop multicast.

## Why video goes over UDP

Measured on a real phone over Wi-Fi with simulated faults on the computer (`netem`, 30 s per run):
TCP and UDP are equal on clean Wi-Fi, but after a Wi-Fi blip TCP replays its backlog and backs off
(p95 delay ~600 ms, frozen 14 s of 30 under 0.5 s outages every 5 s) while UDP is current again at
once (p95 36 ms, frozen 3.7 s). Reordering (jitter) drops TCP to 11 fps; UDP keeps 30.

UDP without resending is worse than TCP: with 5% loss it fell to 1.6 fps, because a recovery
keyframe is ~40 datagrams and almost never arrives whole. NACK-based resending fixes that (28 fps,
p99 105 ms), and resending is skipped for gaps longer than ~165 ms, where it only delivers stale
video. TCP stays as the fallback for networks that drop UDP.

## Pairing

TLS with a pinned phone key, plus a per-computer secret the phone hands out on Allow (details and
the exact rules in [the protocol](protocol.md#identity-and-pairing)). Two choices worth knowing:
the computer has no certificate (Python's stdlib cannot make one, and a secret inside TLS gives the
same protection), and the computer refuses to pair unless its user clicked Connect, which is what
keeps a fake phone from receiving anything on auto-connect. The TLS socket is used from the session
thread only, because an OpenSSL connection must not be read and written by two threads at once.

## Reconnecting

Phones often rejoin Wi-Fi with a **new address** (Android's per-connection random MAC): the test
phone went .143 → .150 in one day. So the window follows the phone by its device id through mDNS
(`reconnect.py`) instead of retrying the old address, and retries failed connects while
reconnecting. On the phone, a session with the same pairing replaces the older one, because
after a Wi-Fi change the older one is dead but would hold the camera until its heartbeat timed out.
A UDP send error is just a lost datagram (`UdpLink.kt`); only the heartbeat or TCP ends a session.

## Video path

- Camera2 renders straight into the encoder's input surface; no pixel data passes through app code.
- The phone prepends SPS/PPS to every keyframe, so the computer can start decoding at any keyframe.
- Latency beats completeness: when the socket backs up, the phone drops frames until the next
  keyframe and asks the encoder for one; the computer's queues are leaky and keep two buffers.
  The phone caps the socket's send buffer, because Android's autotuned buffers hold seconds of
  video during a Wi-Fi stall and replay it afterwards as a long freeze.
- No `videorate` on the computer: it holds each frame until the next arrives (one frame of
  latency). The frame rate is declared in the `appsrc` caps instead.
- The phone sends frames in sensor orientation plus a `rotation`. The computer rotates and then
  scales into a **fixed** output size with black borders. Apps reading a V4L2 device break if its
  format changes mid-stream, so portrait never changes the virtual camera's resolution.
- The virtual camera gets YUY2, the format every consumer we tried accepts.

## Virtual camera

v4l2loopback, because PipeWire cameras are not yet supported by every browser and most
non-browser apps. The device is created with `exclusive_caps=1`; without it Chrome does not list
it. Our helper (`apps/linux/src/beamcam/data/setup-virtual-camera`, installed by the packages as
`/usr/lib/beamcam/setup-virtual-camera` with a polkit action) runs via `pkexec`, finds or
creates a device labelled "Beamcam", and persists the options only when no one else has
configured `v4l2loopback`, so it never fights OBS or a manual setup. Watch out for DroidCam's
`v4l2loopback_dc`, a different module with a similar name.

## Android traps

- The foreground service has type `camera` and is started while the activity is visible; Android 14+
  refuses to start camera services from the background. That is why the service starts when the
  app opens, not when a computer connects.
- The approval prompt is an in-app dialog when the app is visible and a notification with actions
  otherwise. Without notification permission, the user must open the app to approve.
- `targetSdk` stays at 36 for now. Apps targeting Android 17 (API 37) need the runtime
  `ACCESS_LOCAL_NETWORK` permission ([docs](https://developer.android.com/privacy-and-security/local-network-permission)),
  so moving to 37 needs a permission screen for it. `compileSdk` is 37 because current AndroidX requires it.
