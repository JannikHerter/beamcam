# Security

## Trust model

- The phone listens on the local network (TCP 47800) and announces itself with mDNS.
- A computer must be **allowed on the phone** before the camera turns on. Allowed computers are
  remembered by a random id the computer generates (`~/.config/open-webcam/config.json`).
  “Forget allowed computers” on the phone revokes all of them.
- The phone shows a notification for as long as it streams, and Android shows its camera indicator.

## Known limitations (0.1)

- **The video stream is not encrypted** and the computer id is not secret on the wire. Someone on
  the same Wi-Fi could watch the stream, or replay an allowed computer's id. Only use Open Webcam on
  networks you trust (home, your own hotspot) until encrypted pairing lands.
- The virtual camera helper runs as root via `pkexec` (it loads v4l2loopback and writes
  `/etc/modprobe.d/open-webcam.conf` and `/etc/modules-load.d/open-webcam.conf`).

## Reporting

Please report vulnerabilities privately to the maintainers instead of opening a public issue.
