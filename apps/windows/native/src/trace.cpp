#include <cstdarg>
#include <mutex>

#include "shared.h"

namespace bc {

namespace {

bool Enabled() {
    static const bool enabled = [] {
        DWORD value = 0, size = sizeof(value);
        return RegGetValueW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Beamcam", L"CameraLog", RRF_RT_REG_DWORD, nullptr, &value, &size) == ERROR_SUCCESS &&
               value == 1;
    }();
    return enabled;
}

}  // namespace

void Trace(const char* format, ...) {
    if (!Enabled()) return;
    static std::mutex lock;
    char message[1024];
    va_list args;
    va_start(args, format);
    vsnprintf(message, sizeof(message), format, args);
    va_end(args);
    wchar_t path[MAX_PATH];
    const DWORD length = GetTempPathW(MAX_PATH, path);
    if (length == 0 || length > MAX_PATH - 32) return;
    wcscat_s(path, L"beamcam-camera.log");
    std::lock_guard guard(lock);
    FILE* file = nullptr;
    if (_wfopen_s(&file, path, L"a") != 0 || !file) return;
    SYSTEMTIME now;
    GetLocalTime(&now);
    fprintf(file, "%02d:%02d:%02d.%03d [%lu] %s\n", now.wHour, now.wMinute, now.wSecond, now.wMilliseconds, GetCurrentProcessId(), message);
    fclose(file);
}

}  // namespace bc
