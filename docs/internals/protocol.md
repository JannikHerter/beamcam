# Wire protocol (version 2)

The contract between `apps/android` (`Protocol.kt`, `Secure.kt`) and `apps/linux` (`protocol.py`,
`secure.py`). Change both, their tests and this page together. Receivers **ignore unknown message
types**, so new optional messages do not need a version bump; anything an old peer would
misunderstand does. Version 2 added TLS and pairing; a version 1 peer cannot talk to it at all.

## Transport

- The phone listens on TCP **47800** (a random port if that one is taken; the mDNS SRV record and
  the phone's screen show the real one). The connection is **TLS 1.2+** from the first byte.
- Discovery: DNS-SD service `_beamcam._tcp.local`, instance name = phone name,
  TXT `id=<stable phone id>`, `v=2`. Nothing in mDNS is trusted; it only says where to connect.
- One session at a time; a second computer gets `END busy`. A computer that reconnects with the
  same pairing replaces its own older session, which gets `END replaced` (after a Wi-Fi change the
  older one is usually dead and would hold the camera until its heartbeat timed out).

## Identity and pairing

- The phone's TLS certificate is self-signed, with an EC P-256 key kept in the Android keystore.
  Its **fingerprint** is SHA-256 of the certificate (DER). The computer has no certificate.
- The **pairing code** is `u32(fingerprint[0:4]) mod 1 000 000`, shown as `123 456` on the phone's
  approval prompt and on the computer while it waits. Comparing them is optional; it catches a
  machine in the middle on the very first connection.
- **Pairing** happens on the first allowed connection: the phone generates a random 32-byte
  **secret**, sends it in ACCEPT (inside TLS) and stores only its SHA-256. The computer stores
  `fingerprint → secret`. On later connections the computer checks the fingerprint *before* it
  sends anything, then presents the secret in HELLO. A phone with an unknown fingerprint never
  learns a secret.
- The computer pairs with a new fingerprint only when the user asked to connect. Automatic connects
  and reconnects go only to a fingerprint it already knows.

## Framing

Every message is `u8 type | u32 length (big-endian) | payload[length]`. Length is at most 8 MiB.
JSON payloads are UTF-8 objects.

| Type | Name | Direction | Payload |
| ---- | ---- | --------- | ------- |
| 0x01 | HELLO | computer → phone | `{"protocol":2,"clientName":"<hostname>","transports":["udp","tcp"],"secret":"<64 hex>"}`; no `secret` to ask for pairing |
| 0x06 | PENDING | phone → computer | `{}`: the user is being asked to allow this computer (up to 60 s) |
| 0x02 | ACCEPT | phone → computer | `{"protocol":2,"deviceId","deviceName","codec":"h264","width","height","fps","facing"}`, plus `"secret"` when this connection paired, and `"udpPort","udpKey"` when UDP is offered |
| 0x03 | END | phone → computer | `{"reason": …}`, then the phone closes the socket |
| 0x04 | VIDEO | phone → computer | `u64 pts µs | u8 flags | H.264 Annex-B access unit` |
| 0x05 | CAMERA | phone → computer | `{"rotation":0|90|180|270,"facing":"front"|"back"}` |
| 0x10 | SET_CAMERA | computer → phone | `{"facing":"front"|"back"}` |

END reasons: `denied`, `timeout` (nobody answered), `busy`, `stopped` (user stopped on the phone),
`camera` (camera failed), `protocol` (version mismatch), `replaced` (see Transport), `unknown`
(the secret is not one the phone knows: it forgot this computer; the phone does not prompt).

## Session

1. Computer connects, completes TLS and checks the fingerprint, then sends HELLO within 10 s.
2. Known secret: go to 3. No secret: phone sends PENDING and asks the user; on Allow it pairs.
   Unknown secret: `END unknown`.
3. Phone sends ACCEPT, opens the camera, sends CAMERA, then VIDEO continuously.
4. CAMERA is sent again whenever the rotation or camera changes.
5. Either side closes the socket to end. The computer treats 8 s without video as a lost connection.

## UDP video

Video goes over UDP when HELLO lists `"udp"` and ACCEPT answers with `udpPort` and a random
32-byte `udpKey` (hex) for this session only. Control messages stay on TCP. Without that, or if
UDP does not get through, VIDEO messages flow over TLS as before.

1. The computer sends FEEDBACK to the phone's `udpPort` every 100 ms until video arrives. The phone
   streams to the source address of the first FEEDBACK that decrypts and comes from the TCP peer's
   IP, so the computer's firewall treats the video as replies.
2. No FEEDBACK within 1 s after ACCEPT: the phone sends VIDEO over TLS for the whole session.
3. FEEDBACK every second is the heartbeat in both directions: 8 s without one ends the session.

Every datagram is sealed with AES-256-GCM:

`u64 counter | ciphertext | 16 B tag`, nonce = `u32 direction | u64 counter`, no associated data.

`direction` is 1 for phone → computer and 2 for computer → phone, and each sender counts from 0,
so a nonce is never used twice with a session key. Anything that does not decrypt is dropped. The
phone ignores FEEDBACK and NACK whose counter is not higher than the last one it accepted, so old
heartbeats cannot be replayed to keep a session alive; video may arrive reordered and needs no
such check (old frames are discarded by frame id).

Sealed datagrams are at most 1400 bytes, so the plaintext inside is at most 1376 bytes:

| Type | Name | Direction | Plaintext layout |
| ---- | ---- | --------- | ------ |
| 0x01 | VIDEO | phone → computer | `u8 type | u32 frame id | u16 index | u16 count | u8 flags | u64 pts µs | data` (one piece of an access unit) |
| 0x81 | FEEDBACK | computer → phone | `u8 type | u8 flags (bit 0: send a keyframe) | u32 last frame id | u32 losses` |
| 0x82 | NACK | computer → phone | `u8 type | u8 count | count × (u32 frame id, u16 index)`; index 0xFFFF = the whole frame |

The phone keeps the last 90 frames for resending and answers at most one keyframe request per
200 ms. The computer sends NACK as soon as a later packet shows a gap, gives up on a frame after
150 ms, and treats a gap of more than 5 frames as an outage (keyframe right away, no resending of
stale video).

## VIDEO

- `flags`: bit 0 keyframe, bit 1 codec config (never sent alone).
- Every keyframe starts with SPS and PPS, so decoding can begin at any keyframe.
- Frames are in sensor orientation. Rotating clockwise by CAMERA `rotation` makes them upright.
- The phone may skip frames up to the next keyframe when the network is slow.
