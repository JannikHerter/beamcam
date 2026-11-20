// Picture math with no Windows dependency, so it is unit tested on any system.
#pragma once

#include <cstdint>
#include <vector>

namespace bc {

// A view of an NV12 picture someone else owns: full-size Y plane, then half-size interleaved UV.
struct Nv12View {
    const uint8_t* y;
    int yStride;
    const uint8_t* uv;
    int uvStride;
    int width;
    int height;
};

// An NV12 picture with tightly packed planes (stride == width), as sent to the virtual camera.
struct Nv12Frame {
    int width = 0;
    int height = 0;
    std::vector<uint8_t> data;

    Nv12Frame() = default;
    Nv12Frame(int w, int h) : width(w), height(h), data(static_cast<size_t>(w) * h * 3 / 2) {}
    uint8_t* y() { return data.data(); }
    uint8_t* uv() { return data.data() + static_cast<size_t>(width) * height; }
    const uint8_t* y() const { return data.data(); }
    const uint8_t* uv() const { return data.data() + static_cast<size_t>(width) * height; }
    Nv12View view() const { return {y(), width, uv(), width, width, height}; }
};

struct Rect {
    int x;
    int y;
    int width;
    int height;
};

// Where a picture of srcWidth x srcHeight, turned clockwise by `rotation` degrees, lands in an
// outWidth x outHeight output: as large as fits, centered, aspect ratio kept, even coordinates.
Rect FitRect(int srcWidth, int srcHeight, int rotation, int outWidth, int outHeight);

// Turns src clockwise by `rotation` (0, 90, 180, 270) and scales it into out with black borders.
// The output size never changes, because video apps break when a camera's format changes mid-stream.
// The output is always video range (Y 16-235); `fullRange` says the source uses 0-255, as phones'
// encoders usually do.
void RotateInto(const Nv12View& src, int rotation, Nv12Frame& out, bool fullRange = false);

void FillBlack(Nv12Frame& frame);

// Half-size BGRA (width/2 x height/2) for the preview in the window. BT.601, limited range.
void Nv12ToBgraHalf(const Nv12Frame& in, uint8_t* bgra, int stride);

// Full-size BGRA, for snapshots.
void Nv12ToBgra(const Nv12Frame& in, uint8_t* bgra, int stride);

// BGRA of the same size as out (out.width x out.height) to NV12, for the picture shown while no phone streams.
void BgraToNv12(const uint8_t* bgra, int stride, Nv12Frame& out);

}  // namespace bc
