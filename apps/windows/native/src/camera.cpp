// App side of the virtual camera: registers "Beamcam" with Windows for as long as the app runs
// (MFCreateVirtualCamera, Windows 11) and serves the newest frame to each source that connects to
// the pipe. A reader that falls behind gets the newest frame next, never a queue of old ones.
#include <windows.h>
#include <mfapi.h>
#include <mfvirtualcamera.h>
#include <sddl.h>
#include <wrl/client.h>

#include <atomic>
#include <condition_variable>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "beamcam.h"
#include "convert.h"
#include "shared.h"

using Microsoft::WRL::ComPtr;

namespace {

constexpr int kMaxReaders = 4;
// Readers get the current frame again this often even when nothing changed, so a source that
// connected while the app showed a still picture has something to show.
constexpr DWORD kResendMs = 1000;

using Frame = std::shared_ptr<const std::vector<uint8_t>>;

// Only the Frame Server (LOCAL SERVICE), SYSTEM and this user may open the pipe.
std::wstring PipeSecurity() {
    std::wstring user;
    HANDLE token = nullptr;
    if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
        DWORD size = 0;
        GetTokenInformation(token, TokenUser, nullptr, 0, &size);
        std::vector<uint8_t> buffer(size);
        LPWSTR sid = nullptr;
        if (GetTokenInformation(token, TokenUser, buffer.data(), size, &size) &&
            ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(buffer.data())->User.Sid, &sid)) {
            user = sid;
            LocalFree(sid);
        }
        CloseHandle(token);
    }
    std::wstring sddl = L"D:P(A;;GA;;;SY)(A;;GR;;;LS)";
    if (!user.empty()) sddl += L"(A;;GA;;;" + user + L")";
    return sddl;
}

}  // namespace

struct BcCamera {
    ComPtr<IMFVirtualCamera> virtualCamera;
    std::mutex lock;
    std::condition_variable changed;
    Frame current;
    Frame idle;
    uint64_t sequence = 1;
    bool stopping = false;
    std::atomic<int> readers{0};
    HANDLE stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    std::thread acceptor;
    std::vector<std::thread> clients;
    bool mfStarted = false;
    HANDLE owner = nullptr;

    BcCamera() {
        bc::Nv12Frame black(bc::kCameraWidth, bc::kCameraHeight);
        bc::FillBlack(black);
        idle = std::make_shared<std::vector<uint8_t>>(std::move(black.data));
        current = idle;
    }

    void Publish(Frame frame) {
        {
            std::lock_guard guard(lock);
            current = std::move(frame);
            sequence++;
        }
        changed.notify_all();
    }

    void Accept() {
        const std::wstring sddl = PipeSecurity();
        PSECURITY_DESCRIPTOR descriptor = nullptr;
        ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1, &descriptor, nullptr);
        SECURITY_ATTRIBUTES security{sizeof(security), descriptor, FALSE};
        bool first = true;
        while (WaitForSingleObject(stop, 0) != WAIT_OBJECT_0) {
            if (readers >= kMaxReaders) {
                WaitForSingleObject(stop, 200);
                continue;
            }
            // FIRST_PIPE_INSTANCE: if someone else already serves this pipe name, don't share it.
            HANDLE pipe = CreateNamedPipeW(bc::kPipeName,
                                           PIPE_ACCESS_OUTBOUND | FILE_FLAG_OVERLAPPED | (first ? FILE_FLAG_FIRST_PIPE_INSTANCE : 0),
                                           PIPE_TYPE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, kMaxReaders, 1 << 20, 0, 0,
                                           descriptor ? &security : nullptr);
            if (pipe == INVALID_HANDLE_VALUE) {
                bc::Trace("CreateNamedPipe failed (%lu)", GetLastError());
                WaitForSingleObject(stop, 1000);
                continue;
            }
            first = false;
            OVERLAPPED overlapped{};
            overlapped.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
            bool connected = ConnectNamedPipe(pipe, &overlapped) != FALSE;
            if (!connected) {
                const DWORD error = GetLastError();
                if (error == ERROR_PIPE_CONNECTED) {
                    connected = true;
                } else if (error == ERROR_IO_PENDING) {
                    HANDLE handles[] = {stop, overlapped.hEvent};
                    if (WaitForMultipleObjects(2, handles, FALSE, INFINITE) == WAIT_OBJECT_0 + 1) {
                        DWORD ignored;
                        connected = GetOverlappedResult(pipe, &overlapped, &ignored, FALSE) != FALSE;
                    } else {
                        CancelIoEx(pipe, &overlapped);
                        DWORD ignored;
                        GetOverlappedResult(pipe, &overlapped, &ignored, TRUE);
                    }
                }
            }
            CloseHandle(overlapped.hEvent);
            if (!connected) {
                CloseHandle(pipe);
                continue;
            }
            readers++;
            std::lock_guard guard(lock);
            clients.emplace_back([this, pipe] { Serve(pipe); });
        }
        if (descriptor) LocalFree(descriptor);
    }

    // One reader: write the newest frame whenever it changes.
    void Serve(HANDLE pipe) {
        OVERLAPPED overlapped{};
        overlapped.hEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        uint64_t sent = 0;
        std::vector<uint8_t> message(sizeof(bc::FrameHeader) + bc::kFrameBytes);
        const bc::FrameHeader header{bc::kFrameMagic, bc::kCameraWidth, bc::kCameraHeight, bc::kFrameBytes};
        std::memcpy(message.data(), &header, sizeof(header));
        while (true) {
            Frame frame;
            {
                std::unique_lock guard(lock);
                changed.wait_for(guard, std::chrono::milliseconds(kResendMs), [&] { return stopping || sequence != sent; });
                if (stopping) break;
                frame = current;
                sent = sequence;
            }
            std::memcpy(message.data() + sizeof(header), frame->data(), bc::kFrameBytes);
            ResetEvent(overlapped.hEvent);
            if (!WriteFile(pipe, message.data(), static_cast<DWORD>(message.size()), nullptr, &overlapped) &&
                GetLastError() != ERROR_IO_PENDING)
                break;
            HANDLE handles[] = {stop, overlapped.hEvent};
            if (WaitForMultipleObjects(2, handles, FALSE, INFINITE) != WAIT_OBJECT_0 + 1) {
                CancelIoEx(pipe, &overlapped);
                DWORD ignored;
                GetOverlappedResult(pipe, &overlapped, &ignored, TRUE);
                break;
            }
            DWORD written = 0;
            if (!GetOverlappedResult(pipe, &overlapped, &written, FALSE) || written != message.size()) break;
        }
        CloseHandle(overlapped.hEvent);
        DisconnectNamedPipe(pipe);
        CloseHandle(pipe);
        readers--;
    }

    ~BcCamera() {
        {
            std::lock_guard guard(lock);
            stopping = true;
        }
        SetEvent(stop);
        changed.notify_all();
        if (acceptor.joinable()) acceptor.join();
        std::vector<std::thread> done;
        {
            std::lock_guard guard(lock);
            done.swap(clients);
        }
        for (auto& client : done) client.join();
        if (virtualCamera) {
            virtualCamera->Remove();
            virtualCamera.Reset();
        }
        CloseHandle(stop);
        if (mfStarted) MFShutdown();
        if (owner) CloseHandle(owner);
    }
};

namespace bc {
void PublishFrame(BcCamera* camera, const Nv12Frame& frame) {
    camera->Publish(std::make_shared<std::vector<uint8_t>>(frame.data));
}
}  // namespace bc

extern "C" {

HRESULT __stdcall BcCameraCreate(BcCamera** out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    auto camera = std::make_unique<BcCamera>();
    // Windows has one "Beamcam" per session. A second app must not touch it: its cleanup would remove
    // the camera the first one is serving. Windows frees the mutex when the owner exits, even on a crash.
    camera->owner = CreateMutexW(nullptr, FALSE, L"Local\\Beamcam.VirtualCamera");
    if (!camera->owner) return HRESULT_FROM_WIN32(GetLastError());
    if (GetLastError() == ERROR_ALREADY_EXISTS) return HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS);
    HRESULT hr = MFStartup(MF_VERSION, MFSTARTUP_LITE);
    if (FAILED(hr)) return hr;
    camera->mfStarted = true;
    // Serve the pipe before the camera exists, so the first reader finds it.
    camera->acceptor = std::thread([raw = camera.get()] { raw->Accept(); });
    // MFCreateVirtualCamera is looked up at run time: it exists from Windows 11 on.
    using Create = HRESULT(WINAPI*)(MFVirtualCameraType, MFVirtualCameraLifetime, MFVirtualCameraAccess, LPCWSTR, LPCWSTR,
                                    const GUID*, ULONG, IMFVirtualCamera**);
    HMODULE library = LoadLibraryExW(L"mfsensorgroup.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    auto create = library ? reinterpret_cast<Create>(reinterpret_cast<void*>(GetProcAddress(library, "MFCreateVirtualCamera"))) : nullptr;
    if (!create) return HRESULT_FROM_WIN32(ERROR_PROC_NOT_FOUND);
    hr = create(MFVirtualCameraType_SoftwareCameraSource, MFVirtualCameraLifetime_Session, MFVirtualCameraAccess_CurrentUser,
                bc::kCameraName, bc::kSourceClsidString, nullptr, 0, &camera->virtualCamera);
    if (FAILED(hr)) return hr;
    hr = camera->virtualCamera->Start(nullptr);
    if (FAILED(hr)) return hr;
    *out = camera.release();
    return S_OK;
}

void __stdcall BcCameraSetIdleFrame(BcCamera* camera, const uint8_t* bgra, int stride) {
    if (!camera || !bgra) return;
    bc::Nv12Frame frame(bc::kCameraWidth, bc::kCameraHeight);
    bc::BgraToNv12(bgra, stride, frame);
    auto idle = std::make_shared<std::vector<uint8_t>>(std::move(frame.data));
    bool showing;
    {
        std::lock_guard guard(camera->lock);
        showing = camera->current == camera->idle;
        camera->idle = idle;
    }
    if (showing) camera->Publish(idle);
}

void __stdcall BcCameraShowIdle(BcCamera* camera) {
    if (!camera) return;
    Frame idle;
    {
        std::lock_guard guard(camera->lock);
        idle = camera->idle;
    }
    camera->Publish(idle);
}

int __stdcall BcCameraClients(BcCamera* camera) { return camera ? camera->readers.load() : 0; }

void __stdcall BcCameraDestroy(BcCamera* camera) { delete camera; }
}
