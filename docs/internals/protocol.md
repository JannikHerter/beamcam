# Wire protocol (version 1)

The contract between `apps/android` (`Protocol.kt`) and `apps/linux` (`protocol.py`). Change both,
their tests and this page together. Receivers **ignore unknown message types**, so new optional
messages do not need a version bump; anything an old peer would misunderstand does.

## Transport

- The phone listens on TCP **47800** (a random port if that one is taken; the mDNS SRV record and
  the phone's screen show the real one). One session at a time; a second computer gets `END busy`. A
  computer that connects again with the same `clientId` replaces its own older session, which gets
  `END replaced` (after a Wi-Fi change the older one is usually dead and would hold the camera until
  its heartbeat timed out).
- Discovery: DNS-SD service `_openwebcam._tcp.local`, instance name = phone name,
  TXT `id=<stable phone id>`, `v=1`.

## Framing

Every message is `u8 type | u32 length (big-endian) | payload[length]`. Length is at most 8 MiB.
JSON payloads are UTF-8 objects.

| Type | Name | Direction | Payload |
| ---- | ---- | --------- | ------- |
| 0x01 | HELLO | computer → phone | `{"protocol":1,"clientId":"<uuid>","clientName":"<hostname>","transports":["udp","tcp"]}` |
| 0x06 | PENDING | phone → computer | `{}`: the user is being asked to allow this computer (up to 60 s) |
| 0x02 | ACCEPT | phone → computer | `{"protocol":1,"deviceId","deviceName","codec":"h264","width","height","fps","facing"}`, plus `"udpPort","udpToken"` when UDP is offered |
| 0x03 | END | phone → computer | `{"reason": …}`, then the phone closes the socket |
| 0x04 | VIDEO | phone → computer | `u64 pts µs | u8 flags | H.264 Annex-B access unit` |
| 0x05 | CAMERA | phone → computer | `{"rotation":0|90|180|270,"facing":"front"|"back"}` |
| 0x10 | SET_CAMERA | computer → phone | `{"facing":"front"|"back"}` |

END reasons: `denied`, `timeout` (nobody answered), `busy`, `stopped` (user stopped on the phone),
`camera` (camera failed), `protocol` (version mismatch), `replaced` (see below).

## Session

1. Computer connects and sends HELLO within 10 s.
2. Unknown `clientId`: phone sends PENDING and asks the user. Allowed computers skip this.
3. Phone sends ACCEPT, opens the camera, sends CAMERA, then VIDEO continuously.
4. CAMERA is sent again whenever the rotation or camera changes.
5. Either side closes the socket to end. The computer treats 8 s without data as a lost connection.

## UDP video

Video goes over UDP when HELLO lists `"udp"` and ACCEPT answers with `udpPort` and a random
16-byte `udpToken` (hex). Control messages stay on TCP. Without that, or if UDP does not get
through, VIDEO messages flow over TCP as before.

1. The computer sends FEEDBACK to the phone's `udpPort` every 100 ms until video arrives. The phone
   streams to the source address of the first FEEDBACK that carries the token and comes from the
   TCP peer's IP, so the computer's firewall treats the video as replies.
2. No FEEDBACK within 1 s after ACCEPT: the phone sends VIDEO over TCP for the whole session.
3. FEEDBACK every second is the heartbeat in both directions: 8 s without one ends the session.

Datagrams are at most 1400 bytes, integers big-endian.

| Type | Name | Direction | Layout |
| ---- | ---- | --------- | ------ |
| 0x01 | VIDEO | phone → computer | `u32 frame id | u16 index | u16 count | u8 flags | u64 pts µs | data` (one piece of an access unit) |
| 0x81 | FEEDBACK | computer → phone | `16 B token | u8 flags (bit 0: send a keyframe) | u32 last frame id | u32 losses` |
| 0x82 | NACK | computer → phone | `16 B token | u8 count | count × (u32 frame id, u16 index)`; index 0xFFFF = the whole frame |

Each datagram starts with its type byte. The phone keeps the last 90 frames for resending and
answers at most one keyframe request per 200 ms. The computer sends NACK as soon as a later packet
shows a gap, gives up on a frame after 150 ms, and treats a gap of more than 5 frames as an outage
(keyframe right away, no resending of stale video).

## VIDEO

- `flags`: bit 0 keyframe, bit 1 codec config (never sent alone in v1).
- Every keyframe starts with SPS and PPS, so decoding can begin at any keyframe.
- Frames are in sensor orientation. Rotating clockwise by CAMERA `rotation` makes them upright.
- The phone may skip frames up to the next keyframe when the network is slow.
