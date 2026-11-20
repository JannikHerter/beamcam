// Rotation and scaling of the picture: an upright picture in portrait, landscape, front and back
// depends on this. Plain C++, so it runs on Windows (ctest) and on Linux (g++).
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "../src/convert.h"

static int failures = 0;

#define CHECK(cond)                                                       \
    do {                                                                  \
        if (!(cond)) {                                                    \
            std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #cond); \
            failures++;                                                   \
        }                                                                 \
    } while (0)

// A 4x2 source, Y values 1..8 row by row:
//   1 2 3 4
//   5 6 7 8
static bc::Nv12Frame Source() {
    bc::Nv12Frame frame(4, 2);
    for (int i = 0; i < 8; i++) frame.y()[i] = static_cast<uint8_t>(i + 1);
    // Chroma 2x1: (U, V) = (10, 20), (30, 40)
    const uint8_t uv[] = {10, 20, 30, 40};
    for (int i = 0; i < 4; i++) frame.uv()[i] = uv[i];
    return frame;
}

static std::vector<int> Luma(const bc::Nv12Frame& frame) {
    return std::vector<int>(frame.y(), frame.y() + frame.width * frame.height);
}

static void TestFitRect() {
    const bc::Rect same = bc::FitRect(1280, 720, 0, 1280, 720);
    CHECK(same.x == 0 && same.y == 0 && same.width == 1280 && same.height == 720);
    // Portrait: the turned picture is 720 wide and 1280 tall, so it becomes a centered column.
    const bc::Rect portrait = bc::FitRect(1280, 720, 90, 1280, 720);
    CHECK(portrait.height == 720 && portrait.width == 404 && portrait.x == 438 && portrait.y == 0);
    CHECK(bc::FitRect(1280, 720, 270, 1280, 720).width == 404);
    CHECK(bc::FitRect(1280, 720, 180, 1280, 720).width == 1280);
    // A 4:3 picture into 16:9 gets bars left and right; a wider one bars on top and bottom.
    const bc::Rect fourThree = bc::FitRect(640, 480, 0, 1280, 720);
    CHECK(fourThree.width == 960 && fourThree.height == 720 && fourThree.x == 160);
    const bc::Rect wide = bc::FitRect(1920, 600, 0, 1280, 720);
    CHECK(wide.width == 1280 && wide.height == 400 && wide.y == 160);
    // Everything is even, as NV12 needs.
    for (int r : {0, 90, 180, 270}) {
        const bc::Rect fit = bc::FitRect(1000, 563, r, 1280, 720);
        CHECK(fit.x % 2 == 0 && fit.y % 2 == 0 && fit.width % 2 == 0 && fit.height % 2 == 0);
        CHECK(fit.x + fit.width <= 1280 && fit.y + fit.height <= 720);
    }
}

static void TestRotations() {
    const bc::Nv12Frame src = Source();
    {
        bc::Nv12Frame out(4, 2);
        bc::RotateInto(src.view(), 0, out);
        CHECK((Luma(out) == std::vector<int>{1, 2, 3, 4, 5, 6, 7, 8}));
    }
    {
        // Clockwise 90: the bottom-left corner of the sensor image becomes the top-left.
        bc::Nv12Frame out(2, 4);
        bc::RotateInto(src.view(), 90, out);
        CHECK((Luma(out) == std::vector<int>{5, 1, 6, 2, 7, 3, 8, 4}));
        CHECK(out.uv()[0] == 10 && out.uv()[1] == 20 && out.uv()[2] == 30 && out.uv()[3] == 40);
    }
    {
        bc::Nv12Frame out(4, 2);
        bc::RotateInto(src.view(), 180, out);
        CHECK((Luma(out) == std::vector<int>{8, 7, 6, 5, 4, 3, 2, 1}));
        CHECK(out.uv()[0] == 30 && out.uv()[1] == 40 && out.uv()[2] == 10 && out.uv()[3] == 20);
    }
    {
        bc::Nv12Frame out(2, 4);
        bc::RotateInto(src.view(), 270, out);
        CHECK((Luma(out) == std::vector<int>{4, 8, 3, 7, 2, 6, 1, 5}));
        CHECK(out.uv()[0] == 30 && out.uv()[1] == 40 && out.uv()[2] == 10 && out.uv()[3] == 20);
    }
}

// A landscape output with a portrait picture: black bars, the picture itself turned.
static void TestLetterbox() {
    bc::Nv12Frame src(64, 32);
    // Left half dark, right half bright: after turning clockwise, top is dark, bottom bright.
    for (int j = 0; j < 32; j++)
        for (int i = 0; i < 64; i++) src.y()[j * 64 + i] = i < 32 ? 50 : 200;
    std::fill(src.uv(), src.uv() + 64 * 16, 128);
    bc::Nv12Frame out(64, 36);
    bc::RotateInto(src.view(), 90, out);
    const bc::Rect fit = bc::FitRect(64, 32, 90, 64, 36);
    CHECK(fit.width == 18 && fit.height == 36);
    CHECK(out.y()[0] == 16 && out.y()[63] == 16);                  // bars are black
    CHECK(out.uv()[0] == 128 && out.uv()[1] == 128);
    const int mid = fit.x + fit.width / 2;
    CHECK(out.y()[2 * 64 + mid] < 60);                              // top of the picture: dark
    CHECK(out.y()[33 * 64 + mid] > 190);                            // bottom: bright
}

static void TestColors() {
    bc::Nv12Frame white(4, 2), black(4, 2);
    std::fill(white.data.begin(), white.data.begin() + 8, 235);
    std::fill(white.data.begin() + 8, white.data.end(), 128);
    bc::FillBlack(black);
    uint8_t pixels[2 * 4 * 4];
    bc::Nv12ToBgraHalf(white, pixels, 2 * 4);
    CHECK(pixels[0] == 255 && pixels[1] == 255 && pixels[2] == 255 && pixels[3] == 255);
    bc::Nv12ToBgraHalf(black, pixels, 2 * 4);
    CHECK(pixels[0] == 0 && pixels[1] == 0 && pixels[2] == 0);

    // Pure red survives a round trip closely enough for the waiting picture.
    std::vector<uint8_t> red(4 * 2 * 4);
    for (size_t i = 0; i < red.size(); i += 4) {
        red[i] = 0;
        red[i + 1] = 0;
        red[i + 2] = 255;
        red[i + 3] = 255;
    }
    bc::Nv12Frame converted(4, 2);
    bc::BgraToNv12(red.data(), 16, converted);
    std::vector<uint8_t> back(4 * 2 * 4);
    bc::Nv12ToBgra(converted, back.data(), 16);
    CHECK(back[2] > 240 && back[1] < 15 && back[0] < 15);
}

int main() {
    TestFitRect();
    TestRotations();
    TestLetterbox();
    TestColors();
    if (failures) {
        std::fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("convert_tests: all passed\n");
    return 0;
}
