// IMFAttributes forwarded to an inner store made by MFCreateAttributes. The Frame Server expects the
// activator, the source and its streams to be attribute stores themselves.
#pragma once

#define BC_FORWARD_ATTRIBUTES(store)                                                                                       \
    STDMETHODIMP GetItem(REFGUID k, PROPVARIANT* v) override { return store->GetItem(k, v); }                              \
    STDMETHODIMP GetItemType(REFGUID k, MF_ATTRIBUTE_TYPE* t) override { return store->GetItemType(k, t); }               \
    STDMETHODIMP CompareItem(REFGUID k, REFPROPVARIANT v, BOOL* r) override { return store->CompareItem(k, v, r); }       \
    STDMETHODIMP Compare(IMFAttributes* a, MF_ATTRIBUTES_MATCH_TYPE m, BOOL* r) override { return store->Compare(a, m, r); } \
    STDMETHODIMP GetUINT32(REFGUID k, UINT32* v) override { return store->GetUINT32(k, v); }                               \
    STDMETHODIMP GetUINT64(REFGUID k, UINT64* v) override { return store->GetUINT64(k, v); }                               \
    STDMETHODIMP GetDouble(REFGUID k, double* v) override { return store->GetDouble(k, v); }                               \
    STDMETHODIMP GetGUID(REFGUID k, GUID* v) override { return store->GetGUID(k, v); }                                     \
    STDMETHODIMP GetStringLength(REFGUID k, UINT32* l) override { return store->GetStringLength(k, l); }                   \
    STDMETHODIMP GetString(REFGUID k, LPWSTR v, UINT32 n, UINT32* l) override { return store->GetString(k, v, n, l); }    \
    STDMETHODIMP GetAllocatedString(REFGUID k, LPWSTR* v, UINT32* l) override { return store->GetAllocatedString(k, v, l); } \
    STDMETHODIMP GetBlobSize(REFGUID k, UINT32* s) override { return store->GetBlobSize(k, s); }                           \
    STDMETHODIMP GetBlob(REFGUID k, UINT8* b, UINT32 n, UINT32* s) override { return store->GetBlob(k, b, n, s); }        \
    STDMETHODIMP GetAllocatedBlob(REFGUID k, UINT8** b, UINT32* s) override { return store->GetAllocatedBlob(k, b, s); }   \
    STDMETHODIMP GetUnknown(REFGUID k, REFIID i, LPVOID* p) override { return store->GetUnknown(k, i, p); }                \
    STDMETHODIMP SetItem(REFGUID k, REFPROPVARIANT v) override { return store->SetItem(k, v); }                            \
    STDMETHODIMP DeleteItem(REFGUID k) override { return store->DeleteItem(k); }                                           \
    STDMETHODIMP DeleteAllItems() override { return store->DeleteAllItems(); }                                             \
    STDMETHODIMP SetUINT32(REFGUID k, UINT32 v) override { return store->SetUINT32(k, v); }                                \
    STDMETHODIMP SetUINT64(REFGUID k, UINT64 v) override { return store->SetUINT64(k, v); }                                \
    STDMETHODIMP SetDouble(REFGUID k, double v) override { return store->SetDouble(k, v); }                                \
    STDMETHODIMP SetGUID(REFGUID k, REFGUID v) override { return store->SetGUID(k, v); }                                   \
    STDMETHODIMP SetString(REFGUID k, LPCWSTR v) override { return store->SetString(k, v); }                               \
    STDMETHODIMP SetBlob(REFGUID k, const UINT8* b, UINT32 n) override { return store->SetBlob(k, b, n); }                 \
    STDMETHODIMP SetUnknown(REFGUID k, IUnknown* u) override { return store->SetUnknown(k, u); }                           \
    STDMETHODIMP LockStore() override { return store->LockStore(); }                                                       \
    STDMETHODIMP UnlockStore() override { return store->UnlockStore(); }                                                   \
    STDMETHODIMP GetCount(UINT32* c) override { return store->GetCount(c); }                                               \
    STDMETHODIMP GetItemByIndex(UINT32 i, GUID* k, PROPVARIANT* v) override { return store->GetItemByIndex(i, k, v); }    \
    STDMETHODIMP CopyAllItems(IMFAttributes* d) override { return store->CopyAllItems(d); }
