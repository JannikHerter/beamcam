# Security

## Trust model

- The phone listens on the local network (TCP 47800) and announces itself with mDNS. Nothing in
  mDNS is trusted; it only says where to connect.
- Everything on TCP is **TLS**. The phone proves who it is with a key that never leaves the Android
  keystore. The computer pins that key's fingerprint the first time it pairs.
- A computer must be **allowed on the phone** before the camera turns on. On Allow, the phone gives
  the computer a random secret (inside TLS) and keeps only its hash. Later connections present
  the secret, so an allowed computer cannot be impersonated by someone who watched the Wi-Fi.
- The computer checks the fingerprint **before** it sends anything. It pairs with a new phone only
  when its user clicked Connect; automatic connects and reconnects go only to a phone it already
  paired with. A fake phone with a copied name gets nothing.
- Both screens show a 6-digit code on first pairing. Comparing them catches a machine in the middle
  on that first connection; it is optional so beginners are not slowed down.
- Video over UDP is encrypted and authenticated with AES-256-GCM, with a fresh key per session.
- “Allowed computers” on the phone and “Paired Phones” on the computer revoke pairings, one at a time
  or all. Removing a computer that is streaming stops the stream.
- The computer's name on the phone's prompt is whatever that computer says; only the code proves
  anything, and the prompt says so.
- A computer that asks to pair cannot block allowed ones: while its prompt is open, an allowed
  computer that connects takes over and the prompt closes.
- The phone shows a notification for as long as it streams, and Android shows its camera indicator.
- The computer keeps its pairing secrets in `~/.config/beamcam/config.json`, readable only by
  its user. Anyone who can read that file can connect to the paired phones.

## Known limitations

- First pairing is trust on first use: a machine in the middle at that exact moment wins unless the
  codes are compared.
- Replays: the phone ignores replayed UDP control messages, but the computer accepts reordered
  (and therefore replayed) video datagrams; their frames are discarded as old, so this only costs
  bandwidth.
- The virtual camera helper runs as root via `pkexec` (it loads v4l2loopback and writes
  `/etc/modprobe.d/beamcam.conf` and `/etc/modules-load.d/beamcam.conf`).

## Reporting

Please report vulnerabilities privately to the maintainers instead of opening a public issue.
