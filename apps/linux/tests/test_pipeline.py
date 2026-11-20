import os
import tempfile
import unittest

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gst  # noqa: E402

from beamcam.pipeline import Pipeline  # noqa: E402

Gst.init(None)


def encoded_frames(width, height, count=30):
    """Access units like the phone sends: Annex-B, SPS/PPS in front of each keyframe."""
    encoder = "x264enc tune=zerolatency key-int-max=15" if Gst.ElementFactory.find("x264enc") else "openh264enc"
    pipeline = Gst.parse_launch(
        # 8-bit 4:2:0 like a phone's encoder. Left open, newer x264 picks 10-bit 4:4:4, and
        # GStreamer 1.28.7 pads that with pink instead of black (not something phones send).
        f"videotestsrc num-buffers={count} pattern=smpte "
        f"! video/x-raw,format=I420,width={width},height={height},framerate=30/1 "
        f"! {encoder} ! h264parse config-interval=-1 ! video/x-h264,stream-format=byte-stream,alignment=au "
        "! appsink name=out sync=false"
    )
    sink = pipeline.get_by_name("out")
    pipeline.set_state(Gst.State.PLAYING)
    frames = []
    while (sample := sink.emit("try-pull-sample", 5 * Gst.SECOND)) is not None:
        buffer = sample.get_buffer()
        frames.append(buffer.extract_dup(0, buffer.get_size()))
    pipeline.set_state(Gst.State.NULL)
    return frames


@unittest.skipUnless(
    Gst.ElementFactory.find("x264enc") or Gst.ElementFactory.find("openh264enc"), "needs an H.264 encoder to make test video"
)
class PipelineTest(unittest.TestCase):
    def decode(self, rotation, source=(640, 360)):
        pipeline = Pipeline(1280, 720, 30, device=None, snapshots=True)
        pipeline.set_rotation(rotation)
        pipeline.start()
        try:
            for frame in encoded_frames(*source):
                pipeline.push(frame)
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "frame.png")
                self.assertTrue(pipeline.snapshot_png(path))
                image = GdkPixbuf.Pixbuf.new_from_file(path)
            return pipeline.frames_decoded, image
        finally:
            pipeline.stop()

    def test_output_size_is_fixed_when_upright(self):
        decoded, image = self.decode(0)
        self.assertGreater(decoded, 0)
        self.assertEqual((image.get_width(), image.get_height()), (1280, 720))

    def test_portrait_rotation_is_pillarboxed_not_resized(self):
        _decoded, image = self.decode(90)
        self.assertEqual((image.get_width(), image.get_height()), (1280, 720))
        pixels = image.get_pixels()
        row = 720 // 2 * image.get_rowstride()
        # Left edge is a black border (YUV->RGB rounding leaves values near 0); the middle has the pattern.
        self.assertLess(max(pixels[row : row + 3]), 16)
        self.assertGreater(max(pixels[row + 640 * 3 : row + 640 * 3 + 3]), 64)


if __name__ == "__main__":
    unittest.main()
