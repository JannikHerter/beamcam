# Wire protocol (version 1)

The contract between `apps/android` (`Protocol.kt`) and `apps/linux` (`protocol.py`). Change both,
their tests and this page together. Receivers **ignore unknown message types**, so new optional
messages do not need a version bump; anything an old peer would misunderstand does.

## Transport

- The phone listens on TCP **47800** (a random port if that one is taken; the mDNS SRV record and
  the phone's screen show the real one). One session at a time; a second computer gets `END busy`.
- Discovery: DNS-SD service `_openwebcam._tcp.local`, instance name = phone name,
  TXT `id=<stable phone id>`, `v=1`.

## Framing

Every message is `u8 type | u32 length (big-endian) | payload[length]`. Length is at most 8 MiB.
JSON payloads are UTF-8 objects.

| Type | Name | Direction | Payload |
| ---- | ---- | --------- | ------- |
| 0x01 | HELLO | computer → phone | `{"protocol":1,"clientId":"<uuid>","clientName":"<hostname>"}` |
| 0x06 | PENDING | phone → computer | `{}`: the user is being asked to allow this computer (up to 60 s) |
| 0x02 | ACCEPT | phone → computer | `{"protocol":1,"deviceId","deviceName","codec":"h264","width","height","fps","facing"}` |
| 0x03 | END | phone → computer | `{"reason": …}`, then the phone closes the socket |
| 0x04 | VIDEO | phone → computer | `u64 pts µs | u8 flags | H.264 Annex-B access unit` |
| 0x05 | CAMERA | phone → computer | `{"rotation":0|90|180|270,"facing":"front"|"back"}` |
| 0x10 | SET_CAMERA | computer → phone | `{"facing":"front"|"back"}` |

END reasons: `denied`, `timeout` (nobody answered), `busy`, `stopped` (user stopped on the phone),
`camera` (camera failed), `protocol` (version mismatch).

## Session

1. Computer connects and sends HELLO within 10 s.
2. Unknown `clientId`: phone sends PENDING and asks the user. Allowed computers skip this.
3. Phone sends ACCEPT, opens the camera, sends CAMERA, then VIDEO continuously.
4. CAMERA is sent again whenever the rotation or camera changes.
5. Either side closes the socket to end. The computer treats 8 s without data as a lost connection.

## VIDEO

- `flags`: bit 0 keyframe, bit 1 codec config (never sent alone in v1).
- Every keyframe starts with SPS and PPS, so decoding can begin at any keyframe.
- Frames are in sensor orientation. Rotating clockwise by CAMERA `rotation` makes them upright.
- The phone may skip frames up to the next keyframe when the network is slow.
