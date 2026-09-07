#include "runtime.h"
#include "settings.h"
#include "diagnostics.h"
#include <vector>
#include <cstdio>

namespace panthera_sapi {
HMODULE g_module;
bool exact(HANDLE h, void *p, DWORD n, bool write) {
    BYTE *b=(BYTE*)p; DWORD done=0, x;
    while(done<n) {
        BOOL ok=write?WriteFile(h,b+done,n-done,&x,0):ReadFile(h,b+done,n-done,&x,0);
        if(!ok || !x) return false; done+=x;
    }
    return true;
}
std::wstring module_dir() {
    wchar_t p[MAX_PATH]; GetModuleFileNameW(g_module,p,MAX_PATH);
    wchar_t *s=wcsrchr(p,L'\\'); if(s)*s=0; return p;
}
CRITICAL_SECTION g_hostLock;
bool g_lockReady;
HANDLE g_proc, g_in, g_out;
/* What the live host was started with.
 *
 * The engine reads TIGER_PARAMS and TIGER_NO_ABBREV once, in main, before
 * serve mode begins (tiger_host.c ~347) -- they are inherited at spawn and
 * a resident child cannot be told they changed.  Left alone, that makes the
 * Phrasing and Expand-abbreviations controls quietly stop working, which is
 * the one failure this project keeps meeting: a setting that does nothing.
 * So they are remembered here and a difference respawns the host, exactly
 * as pantheradriver.py's _restartHost does for the same two settings.  The
 * tree is here for the same reason -- a generation is a different engine
 * with different data, not a different argument. */
std::wstring g_hostTree, g_hostParams, g_hostAbbrev;
/* Inflection is an embedded [[pmod]] command, and once the channel outlives
 * the utterance, so does the command.  Sending nothing at the default
 * therefore does not mean "the default", it means "whatever was set last".
 * The driver learned that from a user whose volume went to zero and stayed
 * there -- its own comment calls it the worst failure it has had -- so the
 * return to the default is *said*, once, and then not again. */
bool g_inflSent;

void host_drop() {
    if(g_proc){TerminateProcess(g_proc,0);CloseHandle(g_proc);g_proc=0;}
    if(g_in){CloseHandle(g_in);g_in=0;}
    if(g_out){CloseHandle(g_out);g_out=0;}
    g_hostTree.clear();g_hostParams.clear();g_hostAbbrev.clear();
    g_inflSent=false;            /* a new channel starts at the default */
}
bool host_alive() {
    if(!g_proc)return false;
    DWORD code=0;
    if(!GetExitCodeProcess(g_proc,&code)||code!=STILL_ACTIVE){host_drop();return false;}
    return true;
}

/* The host streams audio in chunks it caps at 1024 frames (stream_chunk in
 * tiger_host_serve.c); ten seconds is two hundred times that.  A count above
 * this is not a large chunk, it is a desynced pipe being read as one -- and
 * before this check, `audio.resize(frames*2)` on such a count threw
 * bad_alloc straight through the COM boundary and took the client
 * application down.  The host no longer lets stray output reach the stream,
 * so this is armor rather than the fix: a *small* misread is arithmetic
 * this side cannot detect at all, which is why the host's own redirect is
 * the load-bearing half. */

static DWORD read_timeout_ms() {
    DWORD t = setting_dword(L"ReadTimeoutMs", 30000);
    return t < 1000 ? 1000 : t;
}

/* Read exactly `n` response bytes without ever trusting the host to answer.
 *
 * The old reads blocked in ReadFile with no way out: a host that wedged
 * mid-render -- alive, silent -- held the client's speech thread forever,
 * with the critical section in hand, and the session's speech died with it.
 * Waiting is now a loop that watches four things: data (read it, and any
 * progress resets the clock), the client's abort flag (the one wait SAPI
 * can end early), the host's own death (an empty pipe under a dead host has
 * said everything it ever will), and a deadline with no progress, after
 * which a wedged host is treated as the dead one it is behaving like. */
ReadWait exact_wait(HANDLE h, void *p, DWORD n, ISpTTSEngineSite *site) {
    BYTE *b=(BYTE*)p; DWORD done=0;
    const DWORD budget=read_timeout_ms();
    DWORD idle=GetTickCount();
    while(done<n){
        DWORD avail=0;
        if(!PeekNamedPipe(h,0,0,0,&avail,0))return RW_FAIL;
        if(avail){
            DWORD want=n-done; if(want>avail)want=avail;
            DWORD got=0;
            if(!ReadFile(h,b+done,want,&got,0)||!got)return RW_FAIL;
            done+=got; idle=GetTickCount();
            continue;
        }
        if(site&&(site->GetActions()&SPVES_ABORT))return RW_ABORT;
        if(!g_proc||WaitForSingleObject(g_proc,0)==WAIT_OBJECT_0)return RW_FAIL;
        if(GetTickCount()-idle>=budget)return RW_FAIL;
        Sleep(3);
    }
    return RW_OK;
}
bool host_ensure(const std::wstring &tree, const std::wstring &mt,
                        const std::wstring &sd, const std::wstring &vd,
                        const std::wstring &params, const std::wstring &abbrev) {
    sweep_logs();
    if(host_alive()&&tree==g_hostTree&&params==g_hostParams&&abbrev==g_hostAbbrev)
        return true;
    host_drop();
    SetEnvironmentVariableW(L"TIGER_PARAMS",params.empty()?NULL:params.c_str());
    SetEnvironmentVariableW(L"TIGER_NO_ABBREV",abbrev.empty()?NULL:abbrev.c_str());
    std::wstring cmd=L"\""+module_dir()+L"\\panthera_host.exe\" --serve \""+mt+
                     L"\" \""+sd+L"\" \""+vd+L"\"";
    /* A megabyte of buffer each way, where the default is four kilobytes: a
     * request larger than the buffer would block the writer until the host
     * read it, and the read side never again has to stall the host over a
     * chunk the client has not collected yet. */
    SECURITY_ATTRIBUTES sa={sizeof(sa),0,TRUE}; HANDLE inR,inW,outR,outW;
    if(!CreatePipe(&inR,&inW,&sa,1<<20)||!CreatePipe(&outR,&outW,&sa,1<<20))return false;
    SetHandleInformation(inW,HANDLE_FLAG_INHERIT,0);SetHandleInformation(outR,HANDLE_FLAG_INHERIT,0);
    HANDLE err=host_stderr();
    STARTUPINFOW si={sizeof(si)};si.dwFlags=STARTF_USESTDHANDLES|STARTF_USESHOWWINDOW;si.wShowWindow=SW_HIDE;
    si.hStdInput=inR;si.hStdOutput=outW;
    si.hStdError=err!=INVALID_HANDLE_VALUE?err:GetStdHandle(STD_ERROR_HANDLE);
    PROCESS_INFORMATION pi={}; std::vector<wchar_t> mutableCmd(cmd.begin(),cmd.end());mutableCmd.push_back(0);
    BOOL made=CreateProcessW(0,mutableCmd.data(),0,0,TRUE,CREATE_NO_WINDOW,0,module_dir().c_str(),&si,&pi);
    CloseHandle(inR);CloseHandle(outW);
    if(err!=INVALID_HANDLE_VALUE)CloseHandle(err);
    if(!made){CloseHandle(inW);CloseHandle(outR);return false;}
    CloseHandle(pi.hThread);
    g_proc=pi.hProcess;g_in=inW;g_out=outR;
    g_hostTree=tree;g_hostParams=params;g_hostAbbrev=abbrev;
    logline(L"host started: pid=%u params=\"%.40s\" abbrev=%s tree=\"%.80s\"",
            (unsigned)pi.dwProcessId,params.c_str(),
            abbrev.empty()?L"expand":L"OFF",tree.c_str());
    return true;
}


} // namespace panthera_sapi
