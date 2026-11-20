# Contributing

Thanks for helping! Beamcam is young, so the rules are simple:

- **One problem per pull request.** Small, focused fixes are reviewed fastest.
- **Discuss features first.** Open an issue describing the problem before writing a big change.
- **Bugs:** include your phone model and Android version, your distro, and what the app said.
  `beamcam -v` prints debug logs on the computer; `adb logcat -s WebcamService CameraStreamer` on the phone.
- **Run the checks** in [AGENTS.md](AGENTS.md#verifying) before opening a pull request.
- Changes to the wire format must update both apps and [the protocol](docs/internals/protocol.md).

Build instructions: [development runbook](docs/operations/development.md).
