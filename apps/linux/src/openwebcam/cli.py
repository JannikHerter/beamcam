"""Entry point. No arguments opens the app; subcommands are for scripts and debugging."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from openwebcam import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="open-webcam", description="Use your Android phone as a webcam.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="print debug logs")
    commands = parser.add_subparsers(dest="command")

    discover = commands.add_parser("discover", help="list phones on the local network")
    discover.add_argument("--timeout", type=float, default=5.0, help="seconds to listen (default 5)")

    stream = commands.add_parser("stream", help="stream from a phone without opening a window")
    stream.add_argument("address", help="phone address, e.g. 192.168.1.23 or 192.168.1.23:47800")
    target = stream.add_mutually_exclusive_group()
    target.add_argument("--device", help="v4l2loopback device to write to (default: find it)")
    target.add_argument("--no-camera", action="store_true", help="decode only, do not write a virtual camera")
    stream.add_argument("--frames", type=int, default=0, help="stop after this many decoded frames")
    stream.add_argument("--snapshot", help="save the last frame as PNG when stopping")
    stream.add_argument("--camera", choices=("front", "back"), help="ask the phone to use this camera")
    stream.add_argument("--tcp-only", action="store_true", help="send video over TCP instead of UDP")

    args, gtk_args = parser.parse_known_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO if args.command else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "discover":
        return _discover(args.timeout)
    if args.command == "stream":
        return _stream(args)
    from openwebcam.ui import run

    return run([sys.argv[0], *gtk_args])


def _discover(timeout: float) -> int:
    from openwebcam.mdns import Browser

    found: dict[str, object] = {}

    def on_change(phones):
        for phone in phones:
            if phone.id not in found:
                print(f"{phone.name}\t{phone.host}:{phone.port}\tid={phone.id}", flush=True)
            found[phone.id] = phone

    browser = Browser(on_change)
    browser.start()
    time.sleep(timeout)
    browser.stop()
    if not found:
        print("No phones found. Is Open Webcam open on the phone, on the same Wi-Fi?", file=sys.stderr)
        return 1
    return 0


def _stream(args: argparse.Namespace) -> int:
    from gi.repository import GLib

    from openwebcam import virtualcam
    from openwebcam.client import end_message, parse_address
    from openwebcam.config import Config, computer_name
    from openwebcam.controller import Controller

    try:
        host, port = parse_address(args.address)
    except ValueError as error:
        print(f"Bad address: {error}", file=sys.stderr)
        return 2

    device = None
    if not args.no_camera:
        device = args.device
        if device is None:
            camera = virtualcam.find_camera()
            if camera is None:
                print("No virtual camera found. Open the app once to set it up, or pass --no-camera.", file=sys.stderr)
                return 2
            device = camera.path

    config = Config.load()
    loop = GLib.MainLoop()
    result = {"exit": 0, "snapshot_taken": False}

    class Events:
        def on_pending(self) -> None:
            print("Waiting for you to tap Allow on the phone…", flush=True)

        def on_streaming(self, info, pipeline) -> None:
            print(f"Streaming from {info.device_name} ({info.width}x{info.height}) to {device or 'nowhere'}", flush=True)
            GLib.timeout_add(1500, lambda: print(f"Video transport: {controller.session.transport}", flush=True) and False)
            if args.camera:
                controller.set_camera(args.camera)

        def on_camera(self, facing: str) -> None:
            pass

        def on_ended(self, reason: str) -> None:
            if reason != "closed":
                print(end_message(reason), file=sys.stderr)
                if controller.pipeline_error:
                    print(controller.pipeline_error, file=sys.stderr)
                result["exit"] = 1
            loop.quit()

    controller = Controller(
        host, port, config.client_id, computer_name(), device, Events(),
        snapshots=bool(args.snapshot), transports=("tcp",) if args.tcp_only else ("udp", "tcp"),
    )

    def finish() -> None:
        pipeline = controller.pipeline
        if pipeline is not None and args.snapshot and not result["snapshot_taken"]:
            result["snapshot_taken"] = True
            if pipeline.snapshot_png(args.snapshot):
                print(f"Saved {args.snapshot}", flush=True)
            else:
                print("No frame to save.", file=sys.stderr)
        controller.stop()

    def check_frames() -> bool:
        pipeline = controller.pipeline
        if pipeline is not None and args.frames and pipeline.frames_decoded >= args.frames:
            print(f"Decoded {pipeline.frames_decoded} frames.", flush=True)
            finish()
            return False
        return True

    GLib.timeout_add(100, check_frames)
    controller.start()
    try:
        loop.run()
    except KeyboardInterrupt:
        finish()
        loop.run()
    controller.session.join(2)
    return result["exit"]
