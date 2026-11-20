#include "convert.h"

#include <algorithm>
#include <cstring>

namespace bc {

namespace {

constexpr uint8_t kBlackY = 16;
constexpr uint8_t kBlackUV = 128;

inline uint8_t Clamp8(int v) { return static_cast<uint8_t>(v < 0 ? 0 : v > 255 ? 255 : v); }

// Source position (16.16 fixed point) of output pixel (0, 0) of the fit rectangle and how it moves
// per output step in x and in y. The mapping is affine, so rows are walked with additions only.
struct Walk {
    int64_t sx, sy;          // at the first pixel of the first row
    int64_t xdx, ydx;        // source step for one output pixel to the right
    int64_t xdy, ydy;        // source step for one output row down
};

// rotatedWidth/Height: the picture after turning; fitWidth/Height: its size in the output;
// srcWidth/Height: the source plane. Coordinates are pixel centers.
Walk MakeWalk(int rotation, int srcWidth, int srcHeight, int fitWidth, int fitHeight) {
    const bool sideways = rotation == 90 || rotation == 270;
    const int rotatedWidth = sideways ? srcHeight : srcWidth;
    const int rotatedHeight = sideways ? srcWidth : srcHeight;
    const double kx = static_cast<double>(rotatedWidth) / fitWidth;
    const double ky = static_cast<double>(rotatedHeight) / fitHeight;
    // Position in the turned picture of output pixel (i, j) inside the fit rectangle:
    //   u = (i + 0.5) * kx - 0.5, v = (j + 0.5) * ky - 0.5
    // Turning clockwise by r maps a turned-picture position (u, v) back to the source as:
    //   0: (u, v)   90: (v, H-1-u)   180: (W-1-u, H-1-v)   270: (W-1-v, u)
    const double u0 = 0.5 * kx - 0.5, v0 = 0.5 * ky - 0.5;
    const double w1 = srcWidth - 1, h1 = srcHeight - 1;
    double sx, sy, xdx, ydx, xdy, ydy;
    switch (rotation) {
        case 90:
            sx = v0; sy = h1 - u0; xdx = 0; ydx = -kx; xdy = ky; ydy = 0;
            break;
        case 180:
            sx = w1 - u0; sy = h1 - v0; xdx = -kx; ydx = 0; xdy = 0; ydy = -ky;
            break;
        case 270:
            sx = w1 - v0; sy = u0; xdx = 0; ydx = kx; xdy = -ky; ydy = 0;
            break;
        default:
            sx = u0; sy = v0; xdx = kx; ydx = 0; xdy = 0; ydy = ky;
            break;
    }
    const double one = 65536.0;
    return {static_cast<int64_t>(sx * one + (sx < 0 ? -0.5 : 0.5)),
            static_cast<int64_t>(sy * one + (sy < 0 ? -0.5 : 0.5)),
            static_cast<int64_t>(xdx * one), static_cast<int64_t>(ydx * one),
            static_cast<int64_t>(xdy * one), static_cast<int64_t>(ydy * one)};
}

// Bilinear sample of a plane with `channels` interleaved bytes per pixel.
template <int channels>
inline void Sample(const uint8_t* plane, int stride, int width, int height, int64_t fx, int64_t fy, uint8_t* out) {
    const int64_t maxX = static_cast<int64_t>(width - 1) << 16, maxY = static_cast<int64_t>(height - 1) << 16;
    fx = std::clamp<int64_t>(fx, 0, maxX);
    fy = std::clamp<int64_t>(fy, 0, maxY);
    const int x0 = static_cast<int>(fx >> 16), y0 = static_cast<int>(fy >> 16);
    const int x1 = std::min(x0 + 1, width - 1), y1 = std::min(y0 + 1, height - 1);
    const int ax = static_cast<int>((fx >> 8) & 0xFF), ay = static_cast<int>((fy >> 8) & 0xFF);
    const uint8_t* r0 = plane + static_cast<size_t>(y0) * stride;
    const uint8_t* r1 = plane + static_cast<size_t>(y1) * stride;
    for (int c = 0; c < channels; c++) {
        const int top = r0[x0 * channels + c] * (256 - ax) + r0[x1 * channels + c] * ax;
        const int bottom = r1[x0 * channels + c] * (256 - ax) + r1[x1 * channels + c] * ax;
        out[c] = static_cast<uint8_t>((top * (256 - ay) + bottom * ay + (1 << 15)) >> 16);
    }
}

// Full range (0-255) to video range: Y to 16-235, chroma to 16-240 around 128.
struct RangeTables {
    uint8_t luma[256];
    uint8_t chroma[256];
    RangeTables() {
        for (int v = 0; v < 256; v++) {
            luma[v] = static_cast<uint8_t>(16 + (v * 219 + 127) / 255);
            chroma[v] = static_cast<uint8_t>(128 + ((v - 128) * 224 + (v >= 128 ? 127 : -127)) / 255);
        }
    }
};
const RangeTables kRange;

template <int channels>
void WalkPlane(const uint8_t* src, int srcStride, int srcWidth, int srcHeight, int rotation, uint8_t* dst, int dstStride,
               const Rect& fit, const uint8_t* table) {
    const Walk w = MakeWalk(rotation, srcWidth, srcHeight, fit.width, fit.height);
    for (int j = 0; j < fit.height; j++) {
        int64_t fx = w.sx + w.xdy * j, fy = w.sy + w.ydy * j;
        uint8_t* row = dst + static_cast<size_t>(fit.y + j) * dstStride + static_cast<size_t>(fit.x) * channels;
        for (int i = 0; i < fit.width; i++) {
            uint8_t* pixel = row + i * channels;
            Sample<channels>(src, srcStride, srcWidth, srcHeight, fx, fy, pixel);
            if (table)
                for (int c = 0; c < channels; c++) pixel[c] = table[pixel[c]];
            fx += w.xdx;
            fy += w.ydx;
        }
    }
}

}  // namespace

Rect FitRect(int srcWidth, int srcHeight, int rotation, int outWidth, int outHeight) {
    const bool sideways = rotation == 90 || rotation == 270;
    const int rw = sideways ? srcHeight : srcWidth, rh = sideways ? srcWidth : srcHeight;
    if (rw <= 0 || rh <= 0) return {0, 0, 0, 0};
    int width, height;
    if (static_cast<int64_t>(outWidth) * rh <= static_cast<int64_t>(outHeight) * rw) {
        width = outWidth;
        height = static_cast<int>((static_cast<int64_t>(outWidth) * rh + rw / 2) / rw);
    } else {
        height = outHeight;
        width = static_cast<int>((static_cast<int64_t>(outHeight) * rw + rh / 2) / rh);
    }
    width = std::max(2, std::min(outWidth, width) & ~1);
    height = std::max(2, std::min(outHeight, height) & ~1);
    return {((outWidth - width) / 2) & ~1, ((outHeight - height) / 2) & ~1, width, height};
}

void FillBlack(Nv12Frame& frame) {
    std::memset(frame.y(), kBlackY, static_cast<size_t>(frame.width) * frame.height);
    std::memset(frame.uv(), kBlackUV, static_cast<size_t>(frame.width) * frame.height / 2);
}

namespace {

void CopyRow(uint8_t* dst, const uint8_t* src, int count, const uint8_t* table) {
    if (!table) {
        std::memcpy(dst, src, count);
        return;
    }
    for (int i = 0; i < count; i++) dst[i] = table[src[i]];
}

}  // namespace

void RotateInto(const Nv12View& src, int rotation, Nv12Frame& out, bool fullRange) {
    const Rect fit = FitRect(src.width, src.height, rotation, out.width, out.height);
    if (fit.width != out.width || fit.height != out.height) FillBlack(out);
    const uint8_t* luma = fullRange ? kRange.luma : nullptr;
    const uint8_t* chroma = fullRange ? kRange.chroma : nullptr;
    if (rotation == 0 && fit.width == src.width && fit.height == src.height && fit.x == 0 && fit.y == 0) {
        for (int j = 0; j < src.height; j++)
            CopyRow(out.y() + static_cast<size_t>(j) * out.width, src.y + static_cast<size_t>(j) * src.yStride, src.width, luma);
        for (int j = 0; j < src.height / 2; j++)
            CopyRow(out.uv() + static_cast<size_t>(j) * out.width, src.uv + static_cast<size_t>(j) * src.uvStride, src.width, chroma);
        return;
    }
    WalkPlane<1>(src.y, src.yStride, src.width, src.height, rotation, out.y(), out.width, fit, luma);
    const Rect half{fit.x / 2, fit.y / 2, fit.width / 2, fit.height / 2};
    WalkPlane<2>(src.uv, src.uvStride, src.width / 2, src.height / 2, rotation, out.uv(), out.width, half, chroma);
}

namespace {

inline void YuvToBgra(int y, int u, int v, uint8_t* out) {
    const int c = 298 * (y - 16), d = u - 128, e = v - 128;
    out[0] = Clamp8((c + 516 * d + 128) >> 8);
    out[1] = Clamp8((c - 100 * d - 208 * e + 128) >> 8);
    out[2] = Clamp8((c + 409 * e + 128) >> 8);
    out[3] = 255;
}

}  // namespace

void Nv12ToBgraHalf(const Nv12Frame& in, uint8_t* bgra, int stride) {
    const int w = in.width / 2, h = in.height / 2;
    for (int j = 0; j < h; j++) {
        const uint8_t* y0 = in.y() + static_cast<size_t>(2 * j) * in.width;
        const uint8_t* y1 = y0 + in.width;
        const uint8_t* uv = in.uv() + static_cast<size_t>(j) * in.width;
        uint8_t* row = bgra + static_cast<size_t>(j) * stride;
        for (int i = 0; i < w; i++) {
            const int y = (y0[2 * i] + y0[2 * i + 1] + y1[2 * i] + y1[2 * i + 1] + 2) >> 2;
            YuvToBgra(y, uv[2 * i], uv[2 * i + 1], row + 4 * i);
        }
    }
}

void Nv12ToBgra(const Nv12Frame& in, uint8_t* bgra, int stride) {
    for (int j = 0; j < in.height; j++) {
        const uint8_t* y = in.y() + static_cast<size_t>(j) * in.width;
        const uint8_t* uv = in.uv() + static_cast<size_t>(j / 2) * in.width;
        uint8_t* row = bgra + static_cast<size_t>(j) * stride;
        for (int i = 0; i < in.width; i++) YuvToBgra(y[i], uv[i & ~1], uv[(i & ~1) + 1], row + 4 * i);
    }
}

void BgraToNv12(const uint8_t* bgra, int stride, Nv12Frame& out) {
    for (int j = 0; j < out.height; j++) {
        const uint8_t* row = bgra + static_cast<size_t>(j) * stride;
        uint8_t* y = out.y() + static_cast<size_t>(j) * out.width;
        for (int i = 0; i < out.width; i++) {
            const int b = row[4 * i], g = row[4 * i + 1], r = row[4 * i + 2];
            y[i] = Clamp8(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16);
        }
    }
    for (int j = 0; j < out.height / 2; j++) {
        const uint8_t* r0 = bgra + static_cast<size_t>(2 * j) * stride;
        const uint8_t* r1 = r0 + stride;
        uint8_t* uv = out.uv() + static_cast<size_t>(j) * out.width;
        for (int i = 0; i < out.width / 2; i++) {
            int b = 0, g = 0, r = 0;
            for (const uint8_t* p : {r0 + 8 * i, r0 + 8 * i + 4, r1 + 8 * i, r1 + 8 * i + 4}) {
                b += p[0];
                g += p[1];
                r += p[2];
            }
            b = (b + 2) >> 2;
            g = (g + 2) >> 2;
            r = (r + 2) >> 2;
            uv[2 * i] = Clamp8(((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128);
            uv[2 * i + 1] = Clamp8(((112 * r - 94 * g - 18 * b + 128) >> 8) + 128);
        }
    }
}

}  // namespace bc
