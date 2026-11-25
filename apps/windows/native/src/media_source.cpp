// The "Beamcam" virtual camera's media source. Windows' Frame Server loads this DLL (it runs as
// LOCAL SERVICE, in session 0) when an app opens the camera; the source reads frames the Beamcam app
// serves on a named pipe and hands them out as NV12 samples. Without the app it shows black.
//
// The interface set and attributes follow what the Frame Server asks for, as worked out in Simon
// Mourier's VCamSample (MIT): IMFMediaSourceEx, IMFGetService, IKsControl and IMFSampleAllocatorControl
// on the source, IMFMediaStream2 and IKsControl on the stream, and an IMFActivate in front.
#include <windows.h>
#include <initguid.h>
#include <mfapi.h>
#include <mferror.h>
#include <mfidl.h>
#include <mfvirtualcamera.h>
#include <propvarutil.h>
#include <wrl/client.h>
#include <wrl/implements.h>
// After everything that includes cguid.h: ks.h redefines GUID_NULL in a way cguid.h cannot follow.
#include <ks.h>
#include <ksmedia.h>
#include <ksproxy.h>

#include <atomic>
#include <cstring>
#include <deque>
#include <mutex>
#include <thread>
#include <vector>

#include "attributes.h"
#include "shared.h"

using Microsoft::WRL::ChainInterfaces;
using Microsoft::WRL::ClassicCom;
using Microsoft::WRL::ComPtr;
using Microsoft::WRL::Make;
using Microsoft::WRL::RuntimeClass;
using Microsoft::WRL::RuntimeClassFlags;

namespace bc {

std::atomic<long> g_objects{0};

namespace {

constexpr LONGLONG kFrameDuration = 10'000'000 / kCameraFps;
// Without a new frame from the app, the last one is repeated this often so video apps keep running.
constexpr DWORD kRepeatMs = 100;
constexpr DWORD kReconnectMs = 500;

struct ObjectCount {
    ObjectCount() { g_objects++; }
    ~ObjectCount() { g_objects--; }
};

// Reads frames from the app's pipe into `latest`. Runs on the stream's worker thread.
class PipeReader {
public:
    PipeReader() : event_(CreateEventW(nullptr, TRUE, FALSE, nullptr)) {}
    ~PipeReader() {
        Close();
        CloseHandle(event_);
    }

    HANDLE event() const { return event_; }
    bool connected() const { return pipe_ != INVALID_HANDLE_VALUE; }

    void TryConnect() {
        if (connected()) return;
        pipe_ = CreateFileW(kPipeName, GENERIC_READ, 0, nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr);
        if (pipe_ == INVALID_HANDLE_VALUE) return;
        Trace("pipe connected");
        filled_ = 0;
        buffer_.resize(sizeof(FrameHeader));
        StartRead();
    }

    // After `event()` fired: true when a whole frame is in `frame`.
    bool Complete(std::vector<uint8_t>& frame) {
        DWORD read = 0;
        if (!GetOverlappedResult(pipe_, &overlapped_, &read, FALSE)) {
            if (GetLastError() == ERROR_IO_INCOMPLETE) return false;
            Trace("pipe closed (%lu)", GetLastError());
            Close();
            return false;
        }
        filled_ += read;
        bool done = false;
        if (filled_ == buffer_.size()) {
            if (buffer_.size() == sizeof(FrameHeader)) {
                FrameHeader header;
                std::memcpy(&header, buffer_.data(), sizeof(header));
                if (header.magic != kFrameMagic || header.size > 64u * 1024 * 1024) {
                    Trace("bad frame header");
                    Close();
                    return false;
                }
                width_ = header.width;
                height_ = header.height;
                buffer_.resize(sizeof(FrameHeader) + header.size);
            } else {
                if (width_ == static_cast<uint32_t>(kCameraWidth) && height_ == static_cast<uint32_t>(kCameraHeight) &&
                    buffer_.size() - sizeof(FrameHeader) == kFrameBytes) {
                    frame.assign(buffer_.begin() + sizeof(FrameHeader), buffer_.end());
                    done = true;
                }
                filled_ = 0;
                buffer_.resize(sizeof(FrameHeader));
            }
        }
        StartRead();
        return done;
    }

    void Close() {
        if (!connected()) return;
        CancelIoEx(pipe_, &overlapped_);
        DWORD ignored;
        GetOverlappedResult(pipe_, &overlapped_, &ignored, TRUE);
        CloseHandle(pipe_);
        pipe_ = INVALID_HANDLE_VALUE;
        ResetEvent(event_);
    }

private:
    void StartRead() {
        if (!connected()) return;
        overlapped_ = {};
        overlapped_.hEvent = event_;
        ResetEvent(event_);
        if (!ReadFile(pipe_, buffer_.data() + filled_, static_cast<DWORD>(buffer_.size() - filled_), nullptr, &overlapped_) &&
            GetLastError() != ERROR_IO_PENDING) {
            Trace("pipe read failed (%lu)", GetLastError());
            CloseHandle(pipe_);
            pipe_ = INVALID_HANDLE_VALUE;
            SetEvent(event_);  // the worker loop notices and reconnects later
        }
    }

    HANDLE pipe_ = INVALID_HANDLE_VALUE;
    HANDLE event_;
    OVERLAPPED overlapped_{};
    std::vector<uint8_t> buffer_;
    size_t filled_ = 0;
    uint32_t width_ = 0, height_ = 0;
};

class MediaStream : public RuntimeClass<RuntimeClassFlags<ClassicCom>, ChainInterfaces<IMFMediaStream2, IMFMediaStream, IMFMediaEventGenerator>,
                                        IMFAttributes, IKsControl> {
public:
    HRESULT Initialize(IMFMediaSource* source, DWORD id) {
        source_ = source;
        id_ = id;
        HRESULT hr = MFCreateAttributes(&attributes_, 8);
        if (FAILED(hr)) return hr;
        attributes_->SetGUID(MF_DEVICESTREAM_STREAM_CATEGORY, PINNAME_VIDEO_CAPTURE);
        attributes_->SetUINT32(MF_DEVICESTREAM_STREAM_ID, id);
        attributes_->SetUINT32(MF_DEVICESTREAM_FRAMESERVER_SHARED, 1);
        attributes_->SetUINT32(MF_DEVICESTREAM_ATTRIBUTE_FRAMESOURCE_TYPES, MFFrameSourceTypes_Color);
        hr = MFCreateEventQueue(&queue_);
        if (FAILED(hr)) return hr;

        ComPtr<IMFMediaType> type;
        hr = MFCreateMediaType(&type);
        if (FAILED(hr)) return hr;
        type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
        type->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_NV12);
        type->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
        type->SetUINT32(MF_MT_ALL_SAMPLES_INDEPENDENT, TRUE);
        MFSetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, kCameraWidth, kCameraHeight);
        type->SetUINT32(MF_MT_DEFAULT_STRIDE, kCameraWidth);
        MFSetAttributeRatio(type.Get(), MF_MT_FRAME_RATE, kCameraFps, 1);
        MFSetAttributeRatio(type.Get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1);
        type->SetUINT32(MF_MT_AVG_BITRATE, kFrameBytes * 8 * kCameraFps);
        type->SetUINT32(MF_MT_SAMPLE_SIZE, kFrameBytes);
        // What the app sends: BT.601 colors in video range, whatever range the phone used.
        type->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE, MFNominalRange_16_235);
        type->SetUINT32(MF_MT_YUV_MATRIX, MFVideoTransferMatrix_BT601);
        IMFMediaType* types[] = {type.Get()};
        hr = MFCreateStreamDescriptor(id, 1, types, &descriptor_);
        if (FAILED(hr)) return hr;
        ComPtr<IMFMediaTypeHandler> handler;
        hr = descriptor_->GetMediaTypeHandler(&handler);
        if (FAILED(hr)) return hr;
        hr = handler->SetCurrentMediaType(type.Get());
        if (FAILED(hr)) return hr;
        // Also on the descriptor, for clients that look there.
        attributes_->CopyAllItems(descriptor_.Get());
        latest_.assign(kFrameBytes, 0);
        std::memset(latest_.data() + kCameraWidth * kCameraHeight, 128, kFrameBytes - kCameraWidth * kCameraHeight);
        std::memset(latest_.data(), 16, kCameraWidth * kCameraHeight);
        return S_OK;
    }

    HRESULT GetDescriptor(IMFStreamDescriptor** descriptor) {
        std::lock_guard guard(lock_);
        if (!descriptor_) return MF_E_SHUTDOWN;
        return descriptor_.CopyTo(descriptor);
    }

    void SetAllocator(IMFVideoSampleAllocatorEx* allocator) {
        std::lock_guard guard(lock_);
        allocator_ = allocator;
    }

    HRESULT Start(IMFMediaType* type) {
        {
            std::lock_guard guard(lock_);
            if (!queue_) return MF_E_SHUTDOWN;
            if (type) type_ = type;
            if (allocator_ && type_) {
                HRESULT hr = allocator_->InitializeSampleAllocator(10, type_.Get());
                if (FAILED(hr)) Trace("InitializeSampleAllocator failed 0x%08lx", hr);
            }
            state_ = MF_STREAM_STATE_RUNNING;
        }
        StartWorker();
        Trace("stream started");
        return queue_->QueueEventParamVar(MEStreamStarted, GUID_NULL, S_OK, nullptr);
    }

    HRESULT Stop() {
        StopWorker();
        std::lock_guard guard(lock_);
        if (!queue_) return MF_E_SHUTDOWN;
        if (allocator_) allocator_->UninitializeSampleAllocator();
        tokens_.clear();
        state_ = MF_STREAM_STATE_STOPPED;
        Trace("stream stopped");
        return queue_->QueueEventParamVar(MEStreamStopped, GUID_NULL, S_OK, nullptr);
    }

    void Shutdown() {
        StopWorker();
        std::lock_guard guard(lock_);
        if (queue_) queue_->Shutdown();
        queue_.Reset();
        tokens_.clear();
        allocator_.Reset();
        descriptor_.Reset();
        source_.Reset();
    }

    MF_STREAM_STATE state() {
        std::lock_guard guard(lock_);
        return state_;
    }

    // IMFMediaEventGenerator
    STDMETHODIMP BeginGetEvent(IMFAsyncCallback* callback, IUnknown* state) override {
        auto queue = Queue();
        return queue ? queue->BeginGetEvent(callback, state) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP EndGetEvent(IMFAsyncResult* result, IMFMediaEvent** event) override {
        auto queue = Queue();
        return queue ? queue->EndGetEvent(result, event) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP GetEvent(DWORD flags, IMFMediaEvent** event) override {
        auto queue = Queue();
        return queue ? queue->GetEvent(flags, event) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP QueueEvent(MediaEventType type, REFGUID extended, HRESULT status, const PROPVARIANT* event_value) override {
        auto queue = Queue();
        return queue ? queue->QueueEventParamVar(type, extended, status, event_value) : MF_E_SHUTDOWN;
    }

    // IMFMediaStream
    STDMETHODIMP GetMediaSource(IMFMediaSource** source) override {
        if (!source) return E_POINTER;
        std::lock_guard guard(lock_);
        if (!source_) return MF_E_SHUTDOWN;
        return source_.CopyTo(source);
    }
    STDMETHODIMP GetStreamDescriptor(IMFStreamDescriptor** descriptor) override {
        if (!descriptor) return E_POINTER;
        return GetDescriptor(descriptor);
    }
    STDMETHODIMP RequestSample(IUnknown* token) override {
        {
            std::lock_guard guard(lock_);
            if (!queue_) return MF_E_SHUTDOWN;
            tokens_.emplace_back(token);
        }
        SetEvent(request_);
        return S_OK;
    }

    // IMFMediaStream2
    STDMETHODIMP SetStreamState(MF_STREAM_STATE wanted) override {
        Trace("SetStreamState %d", wanted);
        if (wanted == MF_STREAM_STATE_STOPPED) return state() == MF_STREAM_STATE_STOPPED ? S_OK : Stop();
        {
            std::lock_guard guard(lock_);
            state_ = wanted;
        }
        if (wanted == MF_STREAM_STATE_RUNNING) StartWorker();
        return S_OK;
    }
    STDMETHODIMP GetStreamState(MF_STREAM_STATE* current) override {
        if (!current) return E_POINTER;
        *current = state();
        return S_OK;
    }

    // IKsControl: no camera controls.
    STDMETHOD_(NTSTATUS, KsProperty)(PKSPROPERTY, ULONG, LPVOID, ULONG, ULONG*) override { return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND); }
    STDMETHOD_(NTSTATUS, KsMethod)(PKSMETHOD, ULONG, LPVOID, ULONG, ULONG*) override { return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND); }
    STDMETHOD_(NTSTATUS, KsEvent)(PKSEVENT, ULONG, LPVOID, ULONG, ULONG*) override { return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND); }

    BC_FORWARD_ATTRIBUTES(attributes_)

    ~MediaStream() override {
        StopWorker();
        CloseHandle(request_);
        CloseHandle(stop_);
    }

private:
    ComPtr<IMFMediaEventQueue> Queue() {
        std::lock_guard guard(lock_);
        return queue_;
    }

    void StartWorker() {
        std::lock_guard guard(workerLock_);
        if (worker_.joinable()) return;
        ResetEvent(stop_);
        worker_ = std::thread([this] { Work(); });
    }

    void StopWorker() {
        std::lock_guard guard(workerLock_);
        if (!worker_.joinable()) return;
        SetEvent(stop_);
        worker_.join();
    }

    void Work() {
        CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        PipeReader pipe;
        ULONGLONG lastDelivery = 0, nextConnect = 0;
        uint64_t delivered = 0;
        std::vector<uint8_t> frame;
        while (true) {
            const ULONGLONG now = GetTickCount64();
            if (!pipe.connected() && now >= nextConnect) {
                pipe.TryConnect();
                if (!pipe.connected()) nextConnect = now + kReconnectMs;
            }
            HANDLE handles[] = {stop_, request_, pipe.event()};
            const DWORD count = pipe.connected() ? 3 : 2;
            const DWORD wait = WaitForMultipleObjects(count, handles, FALSE, kRepeatMs / 2);
            if (wait == WAIT_OBJECT_0) break;
            if (wait == WAIT_OBJECT_0 + 2 && pipe.Complete(frame)) {
                std::lock_guard guard(lock_);
                latest_.swap(frame);
                sequence_++;
            }
            // One sample per request: a new frame right away, the last one again after kRepeatMs.
            ComPtr<IUnknown> token;
            bool deliver = false;
            {
                std::lock_guard guard(lock_);
                const ULONGLONG at = GetTickCount64();
                if (!tokens_.empty() && state_ == MF_STREAM_STATE_RUNNING && (sequence_ != delivered || at - lastDelivery >= kRepeatMs)) {
                    token = tokens_.front();
                    tokens_.pop_front();
                    deliver = true;
                    delivered = sequence_;
                    lastDelivery = at;
                }
            }
            if (deliver) Deliver(token.Get());
        }
        CoUninitialize();
    }

    void Deliver(IUnknown* token) {
        ComPtr<IMFSample> sample;
        ComPtr<IMFVideoSampleAllocatorEx> allocator;
        ComPtr<IMFMediaEventQueue> queue;
        {
            std::lock_guard guard(lock_);
            allocator = allocator_;
            queue = queue_;
        }
        if (!queue) return;
        HRESULT hr = allocator ? allocator->AllocateSample(&sample) : E_FAIL;
        if (FAILED(hr)) {
            ComPtr<IMFMediaBuffer> buffer;
            hr = MFCreateSample(&sample);
            if (SUCCEEDED(hr)) hr = MFCreateMemoryBuffer(kFrameBytes, &buffer);
            if (SUCCEEDED(hr)) hr = sample->AddBuffer(buffer.Get());
            if (FAILED(hr)) {
                Trace("cannot make a sample 0x%08lx", hr);
                std::lock_guard guard(lock_);
                tokens_.emplace_front(token);  // try again on the next round
                return;
            }
        }
        ComPtr<IMFMediaBuffer> buffer;
        hr = sample->GetBufferByIndex(0, &buffer);
        if (FAILED(hr)) return;
        {
            std::lock_guard guard(lock_);
            Copy(buffer.Get());
        }
        sample->SetSampleTime(MFGetSystemTime());
        sample->SetSampleDuration(kFrameDuration);
        if (token) sample->SetUnknown(MFSampleExtension_Token, token);
        hr = queue->QueueEventParamUnk(MEMediaSample, GUID_NULL, S_OK, sample.Get());
        if (FAILED(hr)) Trace("QueueEvent(MEMediaSample) failed 0x%08lx", hr);
    }

    // Holds lock_.
    void Copy(IMFMediaBuffer* buffer) {
        ComPtr<IMF2DBuffer2> planes;
        BYTE *scanline = nullptr, *start = nullptr;
        LONG pitch = 0;
        DWORD length = 0;
        if (SUCCEEDED(buffer->QueryInterface(IID_PPV_ARGS(&planes))) &&
            SUCCEEDED(planes->Lock2DSize(MF2DBuffer_LockFlags_Write, &scanline, &pitch, &start, &length))) {
            const uint8_t* source = latest_.data();
            for (int row = 0; row < kCameraHeight * 3 / 2; row++)
                std::memcpy(scanline + static_cast<ptrdiff_t>(row) * pitch, source + static_cast<size_t>(row) * kCameraWidth, kCameraWidth);
            planes->Unlock2D();
            return;
        }
        BYTE* data = nullptr;
        DWORD capacity = 0;
        if (SUCCEEDED(buffer->Lock(&data, &capacity, nullptr))) {
            const DWORD size = capacity < kFrameBytes ? capacity : kFrameBytes;
            std::memcpy(data, latest_.data(), size);
            buffer->Unlock();
            buffer->SetCurrentLength(size);
        }
    }

    ObjectCount count_;
    std::mutex lock_;
    std::mutex workerLock_;
    std::thread worker_;
    HANDLE request_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    HANDLE stop_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    ComPtr<IMFAttributes> attributes_;
    ComPtr<IMFMediaEventQueue> queue_;
    ComPtr<IMFStreamDescriptor> descriptor_;
    ComPtr<IMFMediaSource> source_;
    ComPtr<IMFVideoSampleAllocatorEx> allocator_;
    ComPtr<IMFMediaType> type_;
    std::deque<ComPtr<IUnknown>> tokens_;
    std::vector<uint8_t> latest_;
    uint64_t sequence_ = 0;
    MF_STREAM_STATE state_ = MF_STREAM_STATE_STOPPED;
    DWORD id_ = 0;
};

class MediaSource : public RuntimeClass<RuntimeClassFlags<ClassicCom>, ChainInterfaces<IMFMediaSourceEx, IMFMediaSource, IMFMediaEventGenerator>,
                                        IMFAttributes, IMFGetService, IKsControl, IMFSampleAllocatorControl> {
public:
    HRESULT Initialize(IMFAttributes* activator) {
        HRESULT hr = MFCreateAttributes(&attributes_, 8);
        if (FAILED(hr)) return hr;
        if (activator) activator->CopyAllItems(attributes_.Get());

        ComPtr<IMFSensorProfileCollection> profiles;
        ComPtr<IMFSensorProfile> profile;
        if (SUCCEEDED(MFCreateSensorProfileCollection(&profiles)) && SUCCEEDED(MFCreateSensorProfile(KSCAMERAPROFILE_Legacy, 0, nullptr, &profile))) {
            profile->AddProfileFilter(0, L"((RES==;FRT<=30,1;SUT==))");
            profiles->AddProfile(profile.Get());
            attributes_->SetUnknown(MF_DEVICEMFT_SENSORPROFILE_COLLECTION, profiles.Get());
        }

        stream_ = Make<MediaStream>();
        if (!stream_) return E_OUTOFMEMORY;
        hr = stream_->Initialize(this, 0);
        if (FAILED(hr)) return hr;
        ComPtr<IMFStreamDescriptor> descriptor;
        hr = stream_->GetDescriptor(&descriptor);
        if (FAILED(hr)) return hr;
        IMFStreamDescriptor* descriptors[] = {descriptor.Get()};
        hr = MFCreatePresentationDescriptor(1, descriptors, &presentation_);
        if (FAILED(hr)) return hr;
        return MFCreateEventQueue(&queue_);
    }

    // IMFMediaEventGenerator
    STDMETHODIMP BeginGetEvent(IMFAsyncCallback* callback, IUnknown* state) override {
        auto queue = Queue();
        return queue ? queue->BeginGetEvent(callback, state) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP EndGetEvent(IMFAsyncResult* result, IMFMediaEvent** event) override {
        auto queue = Queue();
        return queue ? queue->EndGetEvent(result, event) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP GetEvent(DWORD flags, IMFMediaEvent** event) override {
        auto queue = Queue();
        return queue ? queue->GetEvent(flags, event) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP QueueEvent(MediaEventType type, REFGUID extended, HRESULT status, const PROPVARIANT* event_value) override {
        auto queue = Queue();
        return queue ? queue->QueueEventParamVar(type, extended, status, event_value) : MF_E_SHUTDOWN;
    }

    // IMFMediaSource
    STDMETHODIMP CreatePresentationDescriptor(IMFPresentationDescriptor** descriptor) override {
        if (!descriptor) return E_POINTER;
        std::lock_guard guard(lock_);
        if (!presentation_) return MF_E_SHUTDOWN;
        return presentation_->Clone(descriptor);
    }
    STDMETHODIMP GetCharacteristics(DWORD* characteristics) override {
        if (!characteristics) return E_POINTER;
        *characteristics = MFMEDIASOURCE_IS_LIVE;
        return S_OK;
    }
    STDMETHODIMP Pause() override { return MF_E_INVALID_STATE_TRANSITION; }
    STDMETHODIMP Shutdown() override {
        Trace("source shutdown");
        std::lock_guard guard(lock_);
        if (!queue_) return MF_E_SHUTDOWN;
        queue_->Shutdown();
        queue_.Reset();
        if (stream_) stream_->Shutdown();
        presentation_.Reset();
        return S_OK;
    }
    STDMETHODIMP Start(IMFPresentationDescriptor* descriptor, const GUID* timeFormat, const PROPVARIANT* position) override {
        if (!descriptor || !position) return E_POINTER;
        if (timeFormat && *timeFormat != GUID_NULL) return MF_E_UNSUPPORTED_TIME_FORMAT;
        std::lock_guard guard(lock_);
        if (!queue_ || !presentation_) return MF_E_SHUTDOWN;
        DWORD count = 0;
        HRESULT hr = descriptor->GetStreamDescriptorCount(&count);
        if (FAILED(hr)) return hr;
        if (count != 1) return E_INVALIDARG;
        BOOL selected = FALSE;
        ComPtr<IMFStreamDescriptor> stream;
        hr = descriptor->GetStreamDescriptorByIndex(0, &selected, &stream);
        if (FAILED(hr)) return hr;
        const bool running = stream_->state() != MF_STREAM_STATE_STOPPED;
        Trace("source start, selected %d, running %d", selected, running);
        if (selected && !running) {
            presentation_->SelectStream(0);
            ComPtr<IUnknown> unknown;
            stream_.As(&unknown);
            queue_->QueueEventParamUnk(MENewStream, GUID_NULL, S_OK, unknown.Get());
            ComPtr<IMFMediaTypeHandler> handler;
            ComPtr<IMFMediaType> type;
            if (SUCCEEDED(stream->GetMediaTypeHandler(&handler))) handler->GetCurrentMediaType(&type);
            hr = stream_->Start(type.Get());
            if (FAILED(hr)) return hr;
        } else if (!selected && running) {
            presentation_->DeselectStream(0);
            stream_->Stop();
        }
        PROPVARIANT time;
        InitPropVariantFromInt64(MFGetSystemTime(), &time);
        hr = queue_->QueueEventParamVar(MESourceStarted, GUID_NULL, S_OK, &time);
        PropVariantClear(&time);
        return hr;
    }
    STDMETHODIMP Stop() override {
        std::lock_guard guard(lock_);
        if (!queue_ || !presentation_) return MF_E_SHUTDOWN;
        if (stream_->state() != MF_STREAM_STATE_STOPPED) stream_->Stop();
        presentation_->DeselectStream(0);
        PROPVARIANT time;
        InitPropVariantFromInt64(MFGetSystemTime(), &time);
        const HRESULT hr = queue_->QueueEventParamVar(MESourceStopped, GUID_NULL, S_OK, &time);
        PropVariantClear(&time);
        return hr;
    }

    // IMFMediaSourceEx
    STDMETHODIMP GetSourceAttributes(IMFAttributes** attributes) override {
        if (!attributes) return E_POINTER;
        return QueryInterface(IID_PPV_ARGS(attributes));
    }
    STDMETHODIMP GetStreamAttributes(DWORD id, IMFAttributes** attributes) override {
        if (!attributes) return E_POINTER;
        if (id != 0) return MF_E_INVALIDSTREAMNUMBER;
        return stream_.CopyTo(attributes);
    }
    STDMETHODIMP SetD3DManager(IUnknown*) override {
        // Frames are in system memory; video apps' pipelines upload them if they want the GPU.
        return S_OK;
    }

    // IMFGetService
    STDMETHODIMP GetService(REFGUID, REFIID, LPVOID* object) override {
        if (object) *object = nullptr;
        return MF_E_UNSUPPORTED_SERVICE;
    }

    // IMFSampleAllocatorControl
    STDMETHODIMP SetDefaultAllocator(DWORD id, IUnknown* allocator) override {
        if (!allocator) return E_POINTER;
        if (id != 0) return MF_E_INVALIDSTREAMNUMBER;
        ComPtr<IMFVideoSampleAllocatorEx> ex;
        const HRESULT hr = allocator->QueryInterface(IID_PPV_ARGS(&ex));
        if (FAILED(hr)) return hr;
        stream_->SetAllocator(ex.Get());
        return S_OK;
    }
    STDMETHODIMP GetAllocatorUsage(DWORD id, DWORD* inputId, MFSampleAllocatorUsage* usage) override {
        if (!inputId || !usage) return E_POINTER;
        if (id != 0) return MF_E_INVALIDSTREAMNUMBER;
        *inputId = id;
        *usage = MFSampleAllocatorUsage_UsesProvidedAllocator;
        return S_OK;
    }

    // IKsControl: no camera controls.
    STDMETHOD_(NTSTATUS, KsProperty)(PKSPROPERTY, ULONG, LPVOID, ULONG, ULONG*) override { return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND); }
    STDMETHOD_(NTSTATUS, KsMethod)(PKSMETHOD, ULONG, LPVOID, ULONG, ULONG*) override { return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND); }
    STDMETHOD_(NTSTATUS, KsEvent)(PKSEVENT, ULONG, LPVOID, ULONG, ULONG*) override { return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND); }

    BC_FORWARD_ATTRIBUTES(attributes_)

private:
    ComPtr<IMFMediaEventQueue> Queue() {
        std::lock_guard guard(lock_);
        return queue_;
    }

    ObjectCount count_;
    std::mutex lock_;
    ComPtr<IMFAttributes> attributes_;
    ComPtr<IMFMediaEventQueue> queue_;
    ComPtr<IMFPresentationDescriptor> presentation_;
    ComPtr<MediaStream> stream_;
};

class Activator : public RuntimeClass<RuntimeClassFlags<ClassicCom>, ChainInterfaces<IMFActivate, IMFAttributes>> {
public:
    HRESULT Initialize() {
        HRESULT hr = MFCreateAttributes(&attributes_, 4);
        if (FAILED(hr)) return hr;
        attributes_->SetUINT32(MF_VIRTUALCAMERA_PROVIDE_ASSOCIATED_CAMERA_SOURCES, 1);
        attributes_->SetGUID(MFT_TRANSFORM_CLSID_Attribute, kSourceClsid);
        source_ = Make<MediaSource>();
        if (!source_) return E_OUTOFMEMORY;
        return source_->Initialize(attributes_.Get());
    }

    STDMETHODIMP ActivateObject(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        std::lock_guard guard(lock_);
        if (!source_) return MF_E_SHUTDOWN;
        return source_.CopyTo(iid, object);
    }
    STDMETHODIMP ShutdownObject() override { return S_OK; }
    STDMETHODIMP DetachObject() override {
        std::lock_guard guard(lock_);
        source_.Reset();
        return S_OK;
    }

    BC_FORWARD_ATTRIBUTES(attributes_)

private:
    ObjectCount count_;
    std::mutex lock_;
    ComPtr<IMFAttributes> attributes_;
    ComPtr<MediaSource> source_;
};

}  // namespace

HRESULT CreateActivator(REFIID iid, void** object) {
    auto activator = Make<Activator>();
    if (!activator) return E_OUTOFMEMORY;
    const HRESULT hr = activator->Initialize();
    if (FAILED(hr)) {
        Trace("activator init failed 0x%08lx", hr);
        return hr;
    }
    return activator.CopyTo(iid, object);
}

}  // namespace bc
