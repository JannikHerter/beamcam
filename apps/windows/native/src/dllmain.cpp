// COM entry points: the Frame Server creates the source through DllGetClassObject. The installer
// registers it (regsvr32 needs administrator rights once, since the Frame Server only reads HKLM).
#include <windows.h>
#include <unknwn.h>

#include <atomic>
#include <string>

#include "shared.h"

namespace bc {
extern std::atomic<long> g_objects;
HRESULT CreateActivator(REFIID iid, void** object);
}  // namespace bc

namespace {

HMODULE g_module = nullptr;
std::atomic<long> g_locks{0};

class ClassFactory final : public IClassFactory {
public:
    STDMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        if (iid == IID_IUnknown || iid == IID_IClassFactory) {
            *object = static_cast<IClassFactory*>(this);
            return S_OK;
        }
        *object = nullptr;
        return E_NOINTERFACE;
    }
    // Static instance: lifetime is the module's.
    STDMETHODIMP_(ULONG) AddRef() override { return 2; }
    STDMETHODIMP_(ULONG) Release() override { return 1; }
    STDMETHODIMP CreateInstance(IUnknown* outer, REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        return bc::CreateActivator(iid, object);
    }
    STDMETHODIMP LockServer(BOOL lock) override {
        lock ? g_locks++ : g_locks--;
        return S_OK;
    }
};

ClassFactory g_factory;

std::wstring ModulePath() {
    std::wstring path(MAX_PATH, L'\0');
    while (true) {
        const DWORD length = GetModuleFileNameW(g_module, path.data(), static_cast<DWORD>(path.size()));
        if (length < path.size()) {
            path.resize(length);
            return path;
        }
        path.resize(path.size() * 2);
    }
}

const std::wstring kClsidKey = std::wstring(L"Software\\Classes\\CLSID\\") + bc::kSourceClsidString;

LSTATUS SetString(HKEY key, const wchar_t* name, const std::wstring& value) {
    return RegSetValueExW(key, name, 0, REG_SZ, reinterpret_cast<const BYTE*>(value.c_str()),
                          static_cast<DWORD>((value.size() + 1) * sizeof(wchar_t)));
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

STDAPI DllGetClassObject(REFCLSID clsid, REFIID iid, LPVOID* object) {
    if (!object) return E_POINTER;
    *object = nullptr;
    if (clsid != bc::kSourceClsid) return CLASS_E_CLASSNOTAVAILABLE;
    bc::Trace("DllGetClassObject");
    return g_factory.QueryInterface(iid, object);
}

STDAPI DllCanUnloadNow() { return bc::g_objects == 0 && g_locks == 0 ? S_OK : S_FALSE; }

STDAPI DllRegisterServer() {
    HKEY key = nullptr;
    LSTATUS status = RegCreateKeyExW(HKEY_LOCAL_MACHINE, kClsidKey.c_str(), 0, nullptr, 0, KEY_WRITE, nullptr, &key, nullptr);
    if (status != ERROR_SUCCESS) return HRESULT_FROM_WIN32(status);
    SetString(key, nullptr, L"Beamcam virtual camera");
    HKEY server = nullptr;
    status = RegCreateKeyExW(key, L"InprocServer32", 0, nullptr, 0, KEY_WRITE, nullptr, &server, nullptr);
    RegCloseKey(key);
    if (status != ERROR_SUCCESS) return HRESULT_FROM_WIN32(status);
    status = SetString(server, nullptr, ModulePath());
    if (status == ERROR_SUCCESS) status = SetString(server, L"ThreadingModel", L"Both");
    RegCloseKey(server);
    return HRESULT_FROM_WIN32(status);
}

STDAPI DllUnregisterServer() {
    const LSTATUS status = RegDeleteTreeW(HKEY_LOCAL_MACHINE, kClsidKey.c_str());
    return status == ERROR_SUCCESS || status == ERROR_FILE_NOT_FOUND ? S_OK : HRESULT_FROM_WIN32(status);
}
