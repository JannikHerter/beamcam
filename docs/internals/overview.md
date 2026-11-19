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

The phone advertises `_openwebcam._tcp` with Android's `NsdManager` (TXT `id` is a stable random
phone id, `v` the protocol version). The computer does **not** use Avahi: it is disabled by
default on Arch and Manjaro, and the D-Bus API adds a dependency. `mdns.py` binds UDP 5353 with
`SO_REUSEPORT` (so it coexists with Avahi when present), joins the group on every IPv4 interface
(VPNs often own the default route) and asks every few seconds. A phone that stops answering
disappears after `STALE_AFTER`; we do not rely on record TTLs, which are an hour long.

The address shown on the phone and **Connect by Address…** are the fallback for networks that
drop multicast.

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
it. Our helper (`apps/linux/src/openwebcam/data/setup-virtual-camera`) runs via `pkexec`, finds or
creates a device labelled "Open Webcam", and persists the options only when no one else has
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
