#pragma once
#include <windows.h>
#include <sapiddk.h>
#include <string>

namespace panthera_sapi {
// Internal DLL runtime: one resident child, serialized across SAPI engines.
// All child state below is accessed with g_hostTurn held (except DLL lifetime).
extern HMODULE g_module;
extern HANDLE g_hostTurn;
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
/* **The host, one voice at a time, each in its turn** (#57, by design).
 * A kernel mutex, not a critical section: released, it passes straight to the
 * voice already waiting, so a voice speaking back to back cannot keep taking
 * the next turn ahead of it -- and a waiter can still wake to see whether it
 * was cancelled.  TurnLock waits for the turn; TurnHeld releases a turn
 * already taken.  An abandoned mutex -- a thread that died holding it -- is
 * still a turn taken. */
struct TurnLock {
    explicit TurnLock(HANDLE value): h(value) { WaitForSingleObject(h, INFINITE); }
    ~TurnLock() { ReleaseMutex(h); }
    TurnLock(const TurnLock &) = delete;
    TurnLock &operator=(const TurnLock &) = delete;
private:
    HANDLE h;
};
struct TurnHeld {
    explicit TurnHeld(HANDLE value): h(value) {}
    ~TurnHeld() { ReleaseMutex(h); }
    TurnHeld(const TurnHeld &) = delete;
    TurnHeld &operator=(const TurnHeld &) = delete;
private:
    HANDLE h;
};
}
