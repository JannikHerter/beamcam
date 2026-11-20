// H.264 from the phone -> Windows' decoder (Media Foundation) -> upright, fixed-size NV12 for the
// virtual camera and a small BGRA preview for the window.
#include <windows.h>
#include <codecapi.h>
#include <mfapi.h>
#include <mferror.h>
#include <mfidl.h>
#include <mftransform.h>
#include <wmcodecdsp.h>
#include <wrl/client.h>

#include <cstring>
#include <mutex>
#include <vector>

#include "beamcam.h"
#include "convert.h"
#include "shared.h"

using Microsoft::WRL::ComPtr;

namespace bc {
void PublishFrame(BcCamera* camera, const Nv12Frame& frame);
}

namespace {

struct ComScope {
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    ~ComScope() {
        if (SUCCEEDED(hr)) CoUninitialize();
    }
};

}  // namespace

struct BcVideo {
    ComPtr<IMFTransform> decoder;
    BcCamera* camera = nullptr;
    bool mfStarted = false;
    bool streaming = false;
    // Decoded picture layout, from the decoder's output type.
    UINT32 frameWidth = 0, frameHeight = 0, displayWidth = 0, displayHeight = 0;
    LONG stride = 0;
    DWORD outputSize = 0;
    bool providesSamples = false;

    bc::Nv12Frame output{bc::kCameraWidth, bc::kCameraHeight};
    std::mutex previewLock;
    std::vector<uint8_t> preview = std::vector<uint8_t>(static_cast<size_t>(bc::kCameraWidth / 2) * (bc::kCameraHeight / 2) * 4);
    bc::Nv12Frame snapshot;
    int64_t frames = 0;

    ~BcVideo() {
        if (decoder) {
            decoder->ProcessMessage(MFT_MESSAGE_NOTIFY_END_STREAMING, 0);
            decoder.Reset();
        }
        if (mfStarted) MFShutdown();
    }

    HRESULT Initialize(int width, int height, int fps) {
        HRESULT hr = MFStartup(MF_VERSION, MFSTARTUP_LITE);
        if (FAILED(hr)) return hr;
        mfStarted = true;
        hr = CoCreateInstance(CLSID_CMSH264DecoderMFT, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&decoder));
        if (FAILED(hr)) return hr;
        ComPtr<IMFAttributes> attributes;
        if (SUCCEEDED(decoder->GetAttributes(&attributes))) {
            // Without this the decoder holds frames back to reorder them; the phone sends none to reorder.
            attributes->SetUINT32(CODECAPI_AVLowLatencyMode, TRUE);
        }
        ComPtr<IMFMediaType> input;
        hr = MFCreateMediaType(&input);
        if (FAILED(hr)) return hr;
        input->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
        input->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_H264);
        input->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
        MFSetAttributeSize(input.Get(), MF_MT_FRAME_SIZE, width, height);
        MFSetAttributeRatio(input.Get(), MF_MT_FRAME_RATE, fps > 0 ? fps : 30, 1);
        hr = decoder->SetInputType(0, input.Get(), 0);
        if (FAILED(hr)) return hr;
        hr = ChooseOutput();
        if (FAILED(hr)) return hr;
        decoder->ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0);
        decoder->ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0);
        return S_OK;
    }

    HRESULT ChooseOutput() {
        for (DWORD i = 0;; i++) {
            ComPtr<IMFMediaType> type;
            HRESULT hr = decoder->GetOutputAvailableType(0, i, &type);
            if (FAILED(hr)) return hr;
            GUID subtype{};
            if (FAILED(type->GetGUID(MF_MT_SUBTYPE, &subtype)) || subtype != MFVideoFormat_NV12) continue;
            hr = decoder->SetOutputType(0, type.Get(), 0);
            if (FAILED(hr)) return hr;
            MFGetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, &frameWidth, &frameHeight);
            displayWidth = frameWidth;
            displayHeight = frameHeight;
            MFVideoArea area{};
            if (SUCCEEDED(type->GetBlob(MF_MT_MINIMUM_DISPLAY_APERTURE, reinterpret_cast<UINT8*>(&area), sizeof(area), nullptr)) &&
                area.Area.cx > 0 && area.Area.cy > 0) {
                displayWidth = static_cast<UINT32>(area.Area.cx);
                displayHeight = static_cast<UINT32>(area.Area.cy);
            }
            UINT32 defaultStride = 0;
            stride = SUCCEEDED(type->GetUINT32(MF_MT_DEFAULT_STRIDE, &defaultStride)) ? static_cast<LONG>(defaultStride)
                                                                                     : static_cast<LONG>(frameWidth);
            MFT_OUTPUT_STREAM_INFO info{};
            decoder->GetOutputStreamInfo(0, &info);
            providesSamples = (info.dwFlags & (MFT_OUTPUT_STREAM_PROVIDES_SAMPLES | MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES)) != 0;
            outputSize = info.cbSize ? info.cbSize : frameWidth * frameHeight * 3 / 2;
            return S_OK;
        }
    }

    HRESULT Push(const uint8_t* data, int size, int64_t ptsUs, int rotation) {
        ComPtr<IMFSample> sample;
        ComPtr<IMFMediaBuffer> buffer;
        HRESULT hr = MFCreateSample(&sample);
        if (SUCCEEDED(hr)) hr = MFCreateMemoryBuffer(static_cast<DWORD>(size), &buffer);
        if (FAILED(hr)) return hr;
        BYTE* target = nullptr;
        hr = buffer->Lock(&target, nullptr, nullptr);
        if (FAILED(hr)) return hr;
        std::memcpy(target, data, size);
        buffer->Unlock();
        buffer->SetCurrentLength(static_cast<DWORD>(size));
        sample->AddBuffer(buffer.Get());
        sample->SetSampleTime(ptsUs * 10);
        hr = decoder->ProcessInput(0, sample.Get(), 0);
        if (hr == MF_E_NOTACCEPTING) {
            hr = Drain(rotation);
            if (FAILED(hr)) return hr;
            hr = decoder->ProcessInput(0, sample.Get(), 0);
        }
        if (FAILED(hr)) return hr;
        return Drain(rotation);
    }

    HRESULT Drain(int rotation) {
        while (true) {
            MFT_OUTPUT_DATA_BUFFER out{};
            ComPtr<IMFSample> mine;
            if (!providesSamples) {
                ComPtr<IMFMediaBuffer> buffer;
                HRESULT hr = MFCreateSample(&mine);
                if (SUCCEEDED(hr)) hr = MFCreateMemoryBuffer(outputSize, &buffer);
                if (SUCCEEDED(hr)) hr = mine->AddBuffer(buffer.Get());
                if (FAILED(hr)) return hr;
                out.pSample = mine.Get();
            }
            DWORD status = 0;
            HRESULT hr = decoder->ProcessOutput(0, 1, &out, &status);
            ComPtr<IMFSample> produced;
            produced.Attach(providesSamples ? out.pSample : nullptr);
            if (out.pEvents) out.pEvents->Release();
            if (hr == MF_E_TRANSFORM_NEED_MORE_INPUT) return S_OK;
            if (hr == MF_E_TRANSFORM_STREAM_CHANGE) {
                hr = ChooseOutput();
                if (FAILED(hr)) return hr;
                continue;
            }
            if (FAILED(hr)) return hr;
            Deliver(providesSamples ? produced.Get() : mine.Get(), rotation);
        }
    }

    void Deliver(IMFSample* sample, int rotation) {
        ComPtr<IMFMediaBuffer> buffer;
        if (FAILED(sample->ConvertToContiguousBuffer(&buffer))) return;
        ComPtr<IMF2DBuffer> planes;
        BYTE* scanline = nullptr;
        LONG pitch = 0;
        bool locked2d = false;
        BYTE* data = nullptr;
        if (SUCCEEDED(buffer.As(&planes)) && SUCCEEDED(planes->Lock2D(&scanline, &pitch))) {
            locked2d = true;
            data = scanline;
        } else if (SUCCEEDED(buffer->Lock(&data, nullptr, nullptr))) {
            pitch = stride;
        } else {
            return;
        }
        const bc::Nv12View view{data, static_cast<int>(pitch), data + static_cast<size_t>(pitch) * frameHeight, static_cast<int>(pitch),
                                static_cast<int>(displayWidth) & ~1, static_cast<int>(displayHeight) & ~1};
        bc::RotateInto(view, rotation, output);
        if (locked2d)
            planes->Unlock2D();
        else
            buffer->Unlock();
        if (camera) bc::PublishFrame(camera, output);
        std::lock_guard guard(previewLock);
        bc::Nv12ToBgraHalf(output, preview.data(), bc::kCameraWidth / 2 * 4);
        snapshot = output;
        frames++;
    }
};

extern "C" {

HRESULT __stdcall BcVideoCreate(int width, int height, int fps, BcCamera* camera, BcVideo** out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    ComScope com;
    auto video = new (std::nothrow) BcVideo();
    if (!video) return E_OUTOFMEMORY;
    video->camera = camera;
    const HRESULT hr = video->Initialize(width, height, fps);
    if (FAILED(hr)) {
        delete video;
        return hr;
    }
    *out = video;
    return S_OK;
}

HRESULT __stdcall BcVideoPush(BcVideo* video, const uint8_t* data, int size, int64_t ptsUs, int rotation) {
    if (!video || !data || size <= 0) return E_INVALIDARG;
    thread_local ComScope com;
    return video->Push(data, size, ptsUs, rotation);
}

int64_t __stdcall BcVideoFrames(BcVideo* video) {
    if (!video) return 0;
    std::lock_guard guard(video->previewLock);
    return video->frames;
}

int64_t __stdcall BcVideoCopyPreview(BcVideo* video, int64_t seen, uint8_t* bgra, int stride) {
    if (!video || !bgra) return 0;
    std::lock_guard guard(video->previewLock);
    if (video->frames == seen) return seen;
    const int rowBytes = bc::kCameraWidth / 2 * 4;
    for (int row = 0; row < bc::kCameraHeight / 2; row++)
        std::memcpy(bgra + static_cast<size_t>(row) * stride, video->preview.data() + static_cast<size_t>(row) * rowBytes, rowBytes);
    return video->frames;
}

HRESULT __stdcall BcVideoSnapshot(BcVideo* video, uint8_t* bgra, int stride) {
    if (!video || !bgra) return E_INVALIDARG;
    std::lock_guard guard(video->previewLock);
    if (video->frames == 0) return S_FALSE;
    bc::Nv12ToBgra(video->snapshot, bgra, stride);
    return S_OK;
}

void __stdcall BcVideoDestroy(BcVideo* video) {
    ComScope com;
    delete video;
}
}
