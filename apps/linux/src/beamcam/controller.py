"""Ties a phone Session to a video Pipeline. Owner callbacks always run on the GLib main thread,
which GTK and the GStreamer bus watch need."""

from __future__ import annotations

import logging
import threading
from typing import Protocol as Interface

from gi.repository import GLib

from beamcam import protocol
from beamcam.client import CLOSED, VIDEO_ERROR, PairingStore, Session, StreamInfo
from beamcam.pipeline import Pipeline, PipelineError

log = logging.getLogger(__name__)

# Frames that arrive before the pipeline is running are kept from the last keyframe on, so
# decoding can start immediately instead of waiting for the phone's next keyframe.
MAX_EARLY_FRAMES = 120


class ControllerEvents(Interface):
    def on_pending(self, code: str) -> None: ...
    def on_streaming(self, info: StreamInfo, pipeline: Pipeline) -> None: ...
    def on_camera(self, facing: str) -> None: ...
    def on_ended(self, reason: str) -> None: ...


class Controller:
    def __init__(
        self,
        host: str,
        port: int,
        client_name: str,
        device: str | None,
        events: ControllerEvents,
        pairings: PairingStore,
        allow_pairing: bool = False,
        preview: bool = False,
        snapshots: bool = False,
        transports: tuple[str, ...] = ("udp", "tcp"),
    ) -> None:
        self._device = device
        self._events = events
        self._preview = preview
        self._snapshots = snapshots
        self._lock = threading.Lock()
        self._pipeline: Pipeline | None = None
        self._early: list[protocol.VideoFrame] = []
        self._rotation = 0
        self._ended = False
        self._pipeline_error: str | None = None
        self.info: StreamInfo | None = None
        self.session = Session(
            host, port, client_name, self, pairings, allow_pairing=allow_pairing, transports=transports
        )

    @property
    def pipeline(self) -> Pipeline | None:
        return self._pipeline

    def start(self) -> None:
        self.session.start()

    def stop(self) -> None:
        """Disconnect; on_ended("closed") follows."""
        self.session.close()

    def set_camera(self, facing: str) -> None:
        self.session.set_camera(facing)

    # SessionListener, called on the session thread.

    def on_pending(self, code: str) -> None:
        GLib.idle_add(self._events.on_pending, code)

    def on_accept(self, info: StreamInfo) -> None:
        self.info = info
        GLib.idle_add(self._start_pipeline, info)

    def on_frame(self, frame: protocol.VideoFrame) -> None:
        with self._lock:
            pipeline = self._pipeline
            if pipeline is None:
                if frame.keyframe:
                    self._early = [frame]
                elif self._early and len(self._early) < MAX_EARLY_FRAMES:
                    self._early.append(frame)
                return
        pipeline.push(frame.data)

    def on_camera(self, rotation: int, facing: str) -> None:
        log.debug("rotation %d, %s camera", rotation, facing)
        with self._lock:
            self._rotation = rotation
            pipeline = self._pipeline
        if pipeline is not None:
            pipeline.set_rotation(rotation)
        GLib.idle_add(self._events.on_camera, facing)

    def on_end(self, reason: str) -> None:
        GLib.idle_add(self._finish, reason)

    # Main thread.

    def _start_pipeline(self, info: StreamInfo) -> bool:
        if self._ended:
            return False
        try:
            pipeline = Pipeline(
                info.width,
                info.height,
                info.fps,
                self._device,
                preview=self._preview,
                snapshots=self._snapshots,
                on_error=self._on_pipeline_error,
            )
            pipeline.start()
        except PipelineError as error:
            self._pipeline_error = str(error)
            self.session.close()
            return False
        with self._lock:
            pipeline.set_rotation(self._rotation)
            for frame in self._early:
                pipeline.push(frame.data)
            self._early = []
            self._pipeline = pipeline
        self._events.on_streaming(info, pipeline)
        return False

    @property
    def pipeline_error(self) -> str | None:
        """Why the video pipeline failed, if that is what ended the session."""
        return self._pipeline_error

    def _on_pipeline_error(self, message: str) -> None:
        self._pipeline_error = message
        self.session.close()

    def _finish(self, reason: str) -> bool:
        if self._ended:
            return False
        self._ended = True
        with self._lock:
            pipeline, self._pipeline = self._pipeline, None
        if pipeline is not None:
            pipeline.stop()
        if self._pipeline_error and reason == CLOSED:
            reason = VIDEO_ERROR
        self._events.on_ended(reason)
        return False
