#pragma once
#include <windows.h>
#include <sapiddk.h>
#include <string>

namespace panthera_sapi {
// Internal DLL runtime: one resident child, serialized across SAPI engines.
// All child state below is accessed with g_hostLock held (except DLL lifetime).
extern HMODULE g_module;
extern CRITICAL_SECTION g_hostLock;
extern bool g_lockReady;
extern HANDLE g_proc, g_in, g_out;
extern std::wstring g_hostTree, g_hostParams, g_hostAbbrev;
extern bool g_inflSent;
const unsigned MAX_CHUNK_FRAMES = 22050u * 10u;
enum ReadWait { RW_OK, RW_FAIL, RW_ABORT };
std::wstring module_dir();
bool exact(HANDLE pipe, void *data, DWORD size, bool write);
ReadWait exact_wait(HANDLE pipe, void *data, DWORD size, ISpTTSEngineSite *site);
void host_drop();
bool host_alive();
bool host_ensure(const std::wstring &tree, const std::wstring &engine,
                 const std::wstring &dictionary, const std::wstring &voices,
                 const std::wstring &params, const std::wstring &abbreviations);
struct CsLock {
    explicit CsLock(CRITICAL_SECTION *value): cs(value) { EnterCriticalSection(cs); }
    ~CsLock() { LeaveCriticalSection(cs); }
    CsLock(const CsLock &) = delete;
    CsLock &operator=(const CsLock &) = delete;
private:
    CRITICAL_SECTION *cs;
};
}
