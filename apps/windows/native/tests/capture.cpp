// A minimal video app for tests: opens a camera by name through Media Foundation, the way browsers
// and video call apps do, reads frames and saves the last one as a BMP.
//   beamcam-capture --list
//   beamcam-capture [--name Beamcam] [--frames 90] [--out frame.bmp]
#include <windows.h>
#include <mfapi.h>
#include <mferror.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <wrl/client.h>

#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;

static int Fail(const char* what, HRESULT hr) {
    std::fprintf(stderr, "%s failed: 0x%08lx\n", what, static_cast<unsigned long>(hr));
    return 1;
}

static bool SaveBmp(const wchar_t* path, const uint8_t* pixels, LONG stride, UINT32 width, UINT32 height) {
    BITMAPFILEHEADER file{};
    BITMAPINFOHEADER info{};
    info.biSize = sizeof(info);
    info.biWidth = static_cast<LONG>(width);
    info.biHeight = -static_cast<LONG>(height);  // top-down rows
    info.biPlanes = 1;
    info.biBitCount = 32;
    info.biCompression = BI_RGB;
    const DWORD rowBytes = width * 4;
    file.bfType = 0x4D42;
    file.bfOffBits = sizeof(file) + sizeof(info);
    file.bfSize = file.bfOffBits + rowBytes * height;
    FILE* out = nullptr;
    if (_wfopen_s(&out, path, L"wb") != 0 || !out) return false;
    std::fwrite(&file, sizeof(file), 1, out);
    std::fwrite(&info, sizeof(info), 1, out);
    for (UINT32 row = 0; row < height; row++) std::fwrite(pixels + static_cast<ptrdiff_t>(row) * stride, 1, rowBytes, out);
    std::fclose(out);
    return true;
}

int wmain(int argc, wchar_t** argv) {
    std::wstring name = L"Beamcam", out = L"frame.bmp";
    int frames = 90;
    bool list = false;
    for (int i = 1; i < argc; i++) {
        const std::wstring arg = argv[i];
        if (arg == L"--list") list = true;
        else if (arg == L"--name" && i + 1 < argc) name = argv[++i];
        else if (arg == L"--frames" && i + 1 < argc) frames = _wtoi(argv[++i]);
        else if (arg == L"--out" && i + 1 < argc) out = argv[++i];
        else {
            std::fprintf(stderr, "usage: beamcam-capture [--list] [--name Beamcam] [--frames 90] [--out frame.bmp]\n");
            return 2;
        }
    }
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    MFStartup(MF_VERSION);

    ComPtr<IMFAttributes> query;
    MFCreateAttributes(&query, 1);
    query->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID);
    IMFActivate** devices = nullptr;
    UINT32 count = 0;
    HRESULT hr = MFEnumDeviceSources(query.Get(), &devices, &count);
    if (FAILED(hr)) return Fail("MFEnumDeviceSources", hr);
    ComPtr<IMFMediaSource> source;
    for (UINT32 i = 0; i < count; i++) {
        wchar_t* friendly = nullptr;
        UINT32 length = 0;
        if (SUCCEEDED(devices[i]->GetAllocatedString(MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME, &friendly, &length))) {
            std::wprintf(L"camera: %s\n", friendly);
            if (!list && !source && name == friendly) {
                hr = devices[i]->ActivateObject(IID_PPV_ARGS(&source));
                if (FAILED(hr)) return Fail("ActivateObject", hr);
            }
            CoTaskMemFree(friendly);
        }
        devices[i]->Release();
    }
    CoTaskMemFree(devices);
    if (list) return 0;
    if (!source) {
        std::fwprintf(stderr, L"no camera named %s\n", name.c_str());
        return 1;
    }

    ComPtr<IMFAttributes> options;
    MFCreateAttributes(&options, 1);
    options->SetUINT32(MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, TRUE);
    ComPtr<IMFSourceReader> reader;
    hr = MFCreateSourceReaderFromMediaSource(source.Get(), options.Get(), &reader);
    if (FAILED(hr)) return Fail("MFCreateSourceReaderFromMediaSource", hr);
    ComPtr<IMFMediaType> native;
    if (SUCCEEDED(reader->GetNativeMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, &native))) {
        GUID subtype{};
        UINT32 w = 0, h = 0;
        native->GetGUID(MF_MT_SUBTYPE, &subtype);
        MFGetAttributeSize(native.Get(), MF_MT_FRAME_SIZE, &w, &h);
        std::printf("native format: %ux%u %s\n", w, h, subtype == MFVideoFormat_NV12 ? "NV12" : "other");
    }
    ComPtr<IMFMediaType> type;
    MFCreateMediaType(&type);
    type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    type->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
    hr = reader->SetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, type.Get());
    if (FAILED(hr)) return Fail("SetCurrentMediaType(RGB32)", hr);
    ComPtr<IMFMediaType> current;
    reader->GetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, &current);
    UINT32 width = 0, height = 0;
    MFGetAttributeSize(current.Get(), MF_MT_FRAME_SIZE, &width, &height);

    ComPtr<IMFSample> last;
    int got = 0;
    auto first = std::chrono::steady_clock::now(), end = first;
    while (got < frames) {
        DWORD stream = 0, flags = 0;
        LONGLONG timestamp = 0;
        ComPtr<IMFSample> sample;
        hr = reader->ReadSample(MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, &stream, &flags, &timestamp, &sample);
        if (FAILED(hr)) return Fail("ReadSample", hr);
        if (flags & MF_SOURCE_READERF_ENDOFSTREAM) break;
        if (!sample) continue;
        if (got == 0) first = std::chrono::steady_clock::now();
        end = std::chrono::steady_clock::now();
        last = sample;
        got++;
    }
    if (!last) {
        std::fprintf(stderr, "no frames\n");
        return 1;
    }
    const double seconds = std::chrono::duration<double>(end - first).count();
    ComPtr<IMFMediaBuffer> buffer;
    last->ConvertToContiguousBuffer(&buffer);
    BYTE* data = nullptr;
    DWORD size = 0;
    buffer->Lock(&data, nullptr, &size);
    uint64_t sum = 0;
    for (DWORD i = 0; i + 3 < size; i += 4) sum += (data[i] + data[i + 1] * 2 + data[i + 2]) / 4;
    const double brightness = size ? static_cast<double>(sum) / (size / 4) : 0;
    const bool saved = SaveBmp(out.c_str(), data, static_cast<LONG>(width * 4), width, height);
    buffer->Unlock();
    std::printf("frames: %d in %.2f s (%.1f fps), %ux%u, mean brightness %.1f\n", got, seconds, got > 1 ? (got - 1) / seconds : 0.0,
                width, height, brightness);
    if (saved) std::wprintf(L"saved %s\n", out.c_str());
    source->Shutdown();
    return 0;
}
