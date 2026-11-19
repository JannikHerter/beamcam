"""GStreamer pipeline: H.264 from the phone -> decoded, rotated, fixed-size video -> virtual camera."""

from __future__ import annotations

import logging
import threading
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

log = logging.getLogger(__name__)

# Software decoders only: the stream is small, and VA-API decoders add setup failures
# without a noticeable gain at 720p.
DECODERS = ("avdec_h264", "openh264dec")

# GstVideoOrientationMethod values for a clockwise rotation in degrees.
_DIRECTION = {0: 0, 90: 1, 180: 2, 270: 3}


class PipelineError(Exception):
    pass


def missing_elements(preview: bool) -> list[str]:
    """Element names we need but cannot find, for an install hint."""
    Gst.init(None)
    needed = ["appsrc", "h264parse", "videoconvert", "videoflip", "videoscale", "tee", "v4l2sink"]
    if preview:
        needed.append("gtk4paintablesink")
    missing = [name for name in needed if Gst.ElementFactory.find(name) is None]
    if not any(Gst.ElementFactory.find(name) for name in DECODERS):
        missing.append("avdec_h264")
    return missing


def _make(factory: str, name: str | None = None, **props) -> Gst.Element:
    element = Gst.ElementFactory.make(factory, name)
    if element is None:
        raise PipelineError(f"GStreamer element '{factory}' is missing")
    for key, value in props.items():
        element.set_property(key.replace("_", "-"), value)
    return element


class Pipeline:
    """Push encoded frames with push(); the output always has the size given at construction,
    so apps reading the virtual camera never see a format change when the phone rotates."""

    def __init__(
        self,
        width: int,
        height: int,
        fps: int,
        device: str | None,
        preview: bool = False,
        snapshots: bool = False,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        Gst.init(None)
        self.width = width
        self.height = height
        self._on_error = on_error
        self._frames = 0
        self._frames_lock = threading.Lock()
        self.paintable = None

        self.pipeline = Gst.Pipeline.new("open-webcam")
        self._src = _make(
            "appsrc",
            "src",
            is_live=True,
            do_timestamp=True,
            format=Gst.Format.TIME,
            # Declaring the rate here instead of using videorate saves a frame of latency:
            # videorate holds every frame until the next one arrives.
            caps=Gst.Caps.from_string(f"video/x-h264,stream-format=byte-stream,alignment=au,framerate={fps}/1"),
            max_bytes=4 * 1024 * 1024,
            leaky_type=2,  # downstream: drop the oldest data if the decoder falls behind
        )
        decoder = self._decoder()
        self._flip = _make("videoflip", "flip")
        chain = [
            self._src,
            _make("h264parse"),
            decoder,
            _make("videoconvert"),
            self._flip,
            _make("videoscale", add_borders=True),
            _make(
                "capsfilter",
                caps=Gst.Caps.from_string(f"video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1"),
            ),
            _make("tee", "tee"),
        ]
        for element in chain:
            self.pipeline.add(element)
        for upstream, downstream in zip(chain, chain[1:]):
            if not upstream.link(downstream):
                raise PipelineError(f"cannot link {upstream.get_name()} -> {downstream.get_name()}")
        tee = chain[-1]

        decoder.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._count_frame)

        if device:
            # YUY2 is the format browsers, Zoom and OBS all accept from a V4L2 camera.
            self._branch(
                tee,
                _make("videoconvert"),
                _make("capsfilter", caps=Gst.Caps.from_string("video/x-raw,format=YUY2")),
                _make("v4l2sink", device=device, sync=False),
            )
        if preview:
            sink = _make("gtk4paintablesink", sync=False)
            self.paintable = sink.get_property("paintable")
            self._branch(tee, _make("videoconvert"), sink)
        self._snapshot_sink = None
        if snapshots:
            self._snapshot_sink = _make("appsink", max_buffers=1, drop=True, sync=False)
            self._branch(
                tee,
                _make("videoconvert"),
                _make("capsfilter", caps=Gst.Caps.from_string("video/x-raw,format=RGB")),
                self._snapshot_sink,
            )
        if not (device or preview or snapshots):
            self._branch(tee, _make("fakesink", sync=False))

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)

    def _decoder(self) -> Gst.Element:
        for name in DECODERS:
            if Gst.ElementFactory.find(name) is None:
                continue
            element = _make(name)
            if name == "avdec_h264":
                # Frame threading buffers several frames before output; slices keep latency at one frame.
                element.set_property("thread-type", 2)
            return element
        raise PipelineError("No H.264 decoder found. Install gst-libav (avdec_h264).")

    def _branch(self, tee: Gst.Element, *elements: Gst.Element) -> None:
        queue = _make("queue", leaky=2, max_size_buffers=2, max_size_bytes=0, max_size_time=0)
        chain = [queue, *elements]
        for element in chain:
            self.pipeline.add(element)
        for upstream, downstream in zip(chain, chain[1:]):
            if not upstream.link(downstream):
                raise PipelineError(f"cannot link {upstream.get_name()} -> {downstream.get_name()}")
        tee.link(queue)

    def _count_frame(self, _pad, _info) -> Gst.PadProbeReturn:
        with self._frames_lock:
            self._frames += 1
        return Gst.PadProbeReturn.OK

    @property
    def frames_decoded(self) -> int:
        with self._frames_lock:
            return self._frames

    def _on_bus_error(self, _bus, message: Gst.Message) -> None:
        error, debug = message.parse_error()
        log.error("pipeline error: %s (%s)", error.message, debug)
        if self._on_error:
            self._on_error(error.message)

    def start(self) -> None:
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise PipelineError("could not start video pipeline (is the camera device in use?)")

    def stop(self) -> None:
        self._src.emit("end-of-stream")
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline.get_bus().remove_signal_watch()

    def push(self, data: bytes) -> None:
        self._src.emit("push-buffer", Gst.Buffer.new_wrapped(data))

    def set_rotation(self, degrees: int) -> None:
        self._flip.set_property("video-direction", _DIRECTION.get(degrees, 0))

    def snapshot_png(self, path: str) -> bool:
        """Save the most recent output frame. Needs snapshots=True."""
        if self._snapshot_sink is None:
            return False
        sample = self._snapshot_sink.emit("try-pull-sample", Gst.SECOND)
        if sample is None:
            return False
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        buffer = sample.get_buffer()
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return False
        try:
            data = GLib.Bytes.new(bytes(info.data))
        finally:
            buffer.unmap(info)
        structure = sample.get_caps().get_structure(0)
        width, height = structure.get_value("width"), structure.get_value("height")
        stride = (width * 3 + 3) & ~3  # GStreamer pads RGB rows to 4 bytes
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, False, 8, width, height, stride)
        pixbuf.savev(path, "png", [], [])
        return True
