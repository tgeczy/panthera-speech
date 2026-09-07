#include "diagnostics.h"
#include "settings.h"
#include <cstdio>
#include <cstdarg>

namespace panthera_sapi {
/* The black box -- **off unless somebody asks for it.**
 *
 * outSPOKEN's afternoon of four COM-layer bugs was settled by exactly this
 * file and nothing else: three theories died of it, and the log convicted
 * in one reading.  That earned it a place here.  It did not earn the place
 * it first took, which was on, always, for everyone.
 *
 * A line per utterance is a line per keystroke, appended forever to a file
 * in %TEMP% that never rotates -- and the line carried the first forty
 * characters of the text.  For a screen reader that is a running
 * transcript of somebody's mail, their messages and their bank, written to
 * a folder anything running as them can read.  Nobody asked for that and
 * nobody would have been told.
 *
 * So: `Diagnostics` in HKCU, default 0, nothing written and no file
 * created.  1 writes the measurements -- counts, bytes, flags, which is
 * what actually convicted -- and 2 adds a slice of the text, for the rare
 * report that is about particular words.  Two deliberate steps to reach
 * the thing with words in it.  Even then the file is capped, because a
 * diagnostic nobody turns off is a disk that fills. */
static const DWORD LOG_CAP = 4u * 1024u * 1024u;

int diagLevel() {
    return (int)setting_dword(L"Diagnostics", 0);
}

/* And clear up after the version that wrote without asking.
 *
 * Anyone who ran a build before this one has a log in %TEMP% still growing
 * a line per utterance, and turning the tap off does not empty the bucket.
 * Once per process, with diagnostics off, our own files go -- only the
 * names this engine writes, only in the temp folder, and only when nothing
 * is meant to be being collected. */
void sweep_logs() {
    static LONG done;
    if(InterlockedExchange(&done,1)||diagLevel())return;
    wchar_t dir[MAX_PATH];
    DWORD n=GetEnvironmentVariableW(L"TEMP",dir,MAX_PATH);
    if(!n||n>=MAX_PATH-48)return;
    const wchar_t *globs[]={L"\\panthera_sapi.log",L"\\panthera_sapi_host-*.log"};
    for(int g=0;g<2;g++){
        wchar_t pat[MAX_PATH]; lstrcpyW(pat,dir); lstrcatW(pat,globs[g]);
        WIN32_FIND_DATAW fd; HANDLE h=FindFirstFileW(pat,&fd);
        if(h==INVALID_HANDLE_VALUE)continue;
        do{
            if(fd.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)continue;
            wchar_t victim[MAX_PATH];
            lstrcpyW(victim,dir); lstrcatW(victim,L"\\"); lstrcatW(victim,fd.cFileName);
            DeleteFileW(victim);
        }while(FindNextFileW(h,&fd));
        FindClose(h);
    }
}
void logline(const wchar_t *fmt, ...) {
    if(!diagLevel())return;
    wchar_t path[MAX_PATH];
    DWORD n=GetEnvironmentVariableW(L"TEMP",path,MAX_PATH);
    if(!n||n>=MAX_PATH-24)return;
    lstrcatW(path,L"\\panthera_sapi.log");
    HANDLE f=CreateFileW(path,FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,
                         0,OPEN_ALWAYS,0,0);
    if(f==INVALID_HANDLE_VALUE)return;
    {   /* Start over rather than grow without end. */
        LARGE_INTEGER sz;
        if(GetFileSizeEx(f,&sz)&&sz.QuadPart>(LONGLONG)LOG_CAP){
            CloseHandle(f);
            f=CreateFileW(path,GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,
                          0,CREATE_ALWAYS,0,0);
            if(f==INVALID_HANDLE_VALUE)return;
        }
    }
    wchar_t line[512];
    va_list ap; va_start(ap,fmt);
    int len=_vsnwprintf_s(line,512,_TRUNCATE,fmt,ap);
    va_end(ap);
    if(len<0)len=511;
    char out[1100]; int m=WideCharToMultiByte(CP_UTF8,0,line,len,out,1060,0,0);
    SYSTEMTIME st; GetLocalTime(&st);
    char stamp[32];
    int sn=sprintf_s(stamp,32,"%02d:%02d:%02d.%03d ",st.wHour,st.wMinute,
                     st.wSecond,st.wMilliseconds);
    DWORD w;
    WriteFile(f,stamp,sn,&w,0);
    WriteFile(f,out,m,&w,0);
    WriteFile(f,"\r\n",2,&w,0);
    CloseHandle(f);
}

/* The host's diagnostics need somewhere that cannot fill up.
 *
 * The NVDA driver hands the child a pipe and spends a thread draining it; a
 * SAPI DLL has no thread to spare, and an undrained pipe stops the writer
 * dead the moment it fills.  For a process that lived one utterance that
 * was unreachable.  For one that lives all session it is a wedge waiting to
 * happen, and it would present as speech stopping for good.
 *
 * So the child never gets a pipe.  With diagnostics off it gets NUL, which
 * discards and cannot block; with them on it gets a file named for the
 * client process, because a 32-bit and a 64-bit SAPI client can be running
 * at once and each has its own host.  What the engine writes there is its
 * own commentary about voices and parameters -- never anything the user
 * asked to have spoken -- but it is off by default all the same, so that a
 * machine nobody is debugging accumulates nothing. */
HANDLE host_stderr() {
    SECURITY_ATTRIBUTES sa={sizeof(sa),0,TRUE};
    wchar_t path[MAX_PATH];
    DWORD n=GetEnvironmentVariableW(L"TEMP",path,MAX_PATH);
    if(!diagLevel()||!n||n>=MAX_PATH-48)
        return CreateFileW(L"NUL",GENERIC_WRITE,
                           FILE_SHARE_READ|FILE_SHARE_WRITE,&sa,OPEN_EXISTING,
                           0,0);
    wchar_t leaf[48];
    swprintf_s(leaf,48,L"\\panthera_sapi_host-%u.log",
               (unsigned)GetCurrentProcessId());
    lstrcatW(path,leaf);
    return CreateFileW(path,FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,
                       &sa,OPEN_ALWAYS,0,0);
}

} // namespace panthera_sapi
