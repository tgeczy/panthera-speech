/* tiger_plat.h -- the POSIX platform seam.
 *
 * This host is written in Win32: it says VirtualAlloc, CreateThread,
 * CRITICAL_SECTION, __declspec(thread).  None of that is because the work is
 * Windows-specific -- it is the loader and shim layer around an emulated i386
 * engine (see tiger_host_uc.c) -- so rather than rewrite fifteen files in a
 * second dialect, this header supplies the Win32 names on top of pthreads,
 * mmap, dlopen and clock_gettime.  The engine's own source never changes; only
 * this file and its implementation (tiger_plat_posix.c) know it is not Windows.
 *
 * Included only when NOT building for Windows (see the top of tiger_host.c),
 * and only ever into that one translation unit.  It must be included before the
 * `#define printf(...)` redirect there, which is why tiger_host.c pulls it in
 * with the rest of the system headers.
 *
 * ARMv7 first, deliberately.  The whole design leans on guest address ==
 * host address as a 32-bit number (tiger_host_uc.c), which is free on a 32-bit
 * ABI and a truncation on a 64-bit one.  armeabi-v7a is 3 of the 4 watches
 * measured and runs on an arm64 phone too; arm64 is a later, separate design.
 */
#ifndef TIGER_PLAT_H
#define TIGER_PLAT_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <pthread.h>
#include <semaphore.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <time.h>
#include <errno.h>
#include <dlfcn.h>
#include <sched.h>           /* sched_yield */
#include <stdio.h>           /* fdopen */
#include <strings.h>          /* strcasecmp, strncasecmp */

/* ---- calling-convention and declspec keywords -------------------------- */
/* The guest's calling convention is emulated inside Unicorn; on the host every
 * function is plain AAPCS, so these MSVC keywords carry no meaning here and
 * expand to nothing -- except __declspec(thread), which is real TLS. */
#define __cdecl
#define __stdcall
#define WINAPI
#define CALLBACK
#define APIENTRY

/* __declspec(x) has two forms in this code: (thread) -> __thread, and
 * (dllexport) -> nothing.  One macro cannot expand to both, so paste the tag. */
#define __declspec(x)            plat_declspec_##x
#define plat_declspec_thread     __thread
#define plat_declspec_dllexport
#define plat_declspec_dllimport
#define plat_declspec_noreturn

/* ---- scalar and pointer types ------------------------------------------ */
typedef int                 BOOL;
typedef unsigned int        DWORD;      /* Win32 DWORD is exactly 32 bits */
typedef long                LONG;
typedef unsigned long       ULONG;
typedef unsigned char       BYTE;
typedef unsigned short      WORD;
typedef unsigned int        UINT;
typedef void               *LPVOID;
typedef const void         *LPCVOID;
typedef char               *LPSTR;
typedef const char         *LPCSTR;
typedef void               *HANDLE;
typedef void               *HMODULE;
typedef void               *HLOCAL;
typedef uintptr_t           ULONG_PTR;
typedef uintptr_t           DWORD_PTR;
typedef intptr_t            INT_PTR;
typedef uintptr_t           UINT_PTR;

#define __int64             long long
#define VOID                void
#define CONST               const

#ifndef TRUE
#define TRUE  1
#define FALSE 0
#endif
#ifndef MAX_PATH
#define MAX_PATH 1024
#endif

/* ---- constants --------------------------------------------------------- */
#define INFINITE                0xFFFFFFFFu
#define WAIT_OBJECT_0           0x00000000u
#define WAIT_TIMEOUT            0x00000102u
#define WAIT_FAILED             0xFFFFFFFFu

#define MEM_COMMIT              0x00001000u
#define MEM_RESERVE             0x00002000u
#define MEM_DECOMMIT            0x00004000u
#define MEM_RELEASE             0x00008000u

#define PAGE_NOACCESS           0x01u
#define PAGE_READONLY           0x02u
#define PAGE_READWRITE          0x04u
#define PAGE_WRITECOPY          0x08u
#define PAGE_EXECUTE            0x10u
#define PAGE_EXECUTE_READ       0x20u
#define PAGE_EXECUTE_READWRITE  0x40u

#define FILE_MAP_COPY           0x0001u
#define FILE_MAP_READ           0x0004u

#define GENERIC_READ            0x80000000u
#define FILE_READ_ATTRIBUTES    0x0080u
#define FILE_SHARE_READ         0x0001u
#define FILE_SHARE_WRITE        0x0002u
#define FILE_SHARE_DELETE       0x0004u
#define OPEN_EXISTING           3u
#define FILE_FLAG_BACKUP_SEMANTICS 0x02000000u
#define INVALID_HANDLE_VALUE    ((HANDLE)(intptr_t)-1)

#define ERROR_HANDLE_EOF        38u
#define TLS_OUT_OF_INDEXES      0xFFFFFFFFu
#define SYNCHRONIZE             0x00100000u

#define _O_BINARY               0
#ifndef _O_RDONLY
#define _O_RDONLY               O_RDONLY
#endif
#ifndef _O_WRONLY
#define _O_WRONLY               O_WRONLY
#endif
#ifndef _O_RDWR
#define _O_RDWR                 O_RDWR
#endif
#define _S_IFDIR                S_IFDIR

/* ---- aggregate types --------------------------------------------------- */
/* A CRITICAL_SECTION must be recursive: the engine re-enters locks it already
 * holds (see the note in tiger_host_shims.c), and a default mutex would
 * deadlock the first time it did.  InitializeCriticalSection sets that up. */
typedef pthread_mutex_t     CRITICAL_SECTION;
typedef pthread_cond_t      CONDITION_VARIABLE;

typedef struct {
    unsigned    dwOemId;
    unsigned    dwPageSize;
    void       *lpMinimumApplicationAddress;
    void       *lpMaximumApplicationAddress;
    ULONG_PTR   dwActiveProcessorMask;
    unsigned    dwNumberOfProcessors;
    unsigned    dwProcessorType;
    unsigned    dwAllocationGranularity;
    unsigned short wProcessorLevel;
    unsigned short wProcessorRevision;
} SYSTEM_INFO;

typedef struct {
    ULONG_PTR   Internal;
    ULONG_PTR   InternalHigh;
    DWORD       Offset;
    DWORD       OffsetHigh;
    HANDLE      hEvent;
} OVERLAPPED;

typedef struct {
    DWORD   dwFileAttributes;
    DWORD   ftCreationTime[2];
    DWORD   ftLastAccessTime[2];
    DWORD   ftLastWriteTime[2];
    DWORD   dwVolumeSerialNumber;
    DWORD   nFileSizeHigh;
    DWORD   nFileSizeLow;
    DWORD   nNumberOfLinks;
    DWORD   nFileIndexHigh;
    DWORD   nFileIndexLow;
} BY_HANDLE_FILE_INFORMATION;

typedef union {
    struct { DWORD LowPart; LONG HighPart; } u;
    long long QuadPart;
} LARGE_INTEGER;

typedef struct { DWORD dwLowDateTime; DWORD dwHighDateTime; } FILETIME;

/* ---- interlocked: __atomic builtins are type-generic, so one spelling
 * serves every LONG/long/unsigned/pointer target in the tree, and each returns
 * exactly what Win32 promises: Increment/Decrement the NEW value (cf_release's
 * `<= 0` and the refcount masks depend on it), ExchangeAdd the OLD. ---------*/
#define InterlockedIncrement(p)          __atomic_add_fetch((p), 1, __ATOMIC_SEQ_CST)
#define InterlockedDecrement(p)          __atomic_sub_fetch((p), 1, __ATOMIC_SEQ_CST)
#define InterlockedExchangeAdd(p, n)     __atomic_fetch_add((p), (n), __ATOMIC_SEQ_CST)
#define InterlockedExchange(p, v)        __atomic_exchange_n((p), (v), __ATOMIC_SEQ_CST)
#define InterlockedExchangePointer(p, v) __atomic_exchange_n((p), (v), __ATOMIC_SEQ_CST)

#define MemoryBarrier()                  __sync_synchronize()

/* ---- CRT spellings this host uses -------------------------------------- */
#define _stricmp    strcasecmp
#define _strnicmp   strncasecmp
#define _snprintf   snprintf
#define _vsnprintf  vsnprintf
#define _access     access
#define _stat64     stat
#define _lseek      lseek
#define _read       read
#define _write      write
#define _close      close
#define _open       open
#define _dup        dup
#define _dup2       dup2
#define _fileno     fileno
#define _fstat64    fstat
#define _fdopen     fdopen
#define _setmode(fd, mode)  (0)

/* Win32 SwitchToThread yielded to a ready thread; with the pacer's 1 ms timer
 * that behaved like a brief sleep.  On bionic sched_yield returns at once when
 * nothing else is runnable, so an idle-yield path would spin a whole core --
 * and on a two-core watch starve the TCG worker that is actually rendering,
 * which comes out as short slices and a wrong frame count.  A real 1 ms sleep
 * is what Windows effectively delivered.  (Sleep is declared below.) */
#define SwitchToThread()    (Sleep(1), 1)

/* timeBeginPeriod/timeEndPeriod raise the scheduler's tick resolution on
 * Windows; POSIX nanosleep already means what it says, so these are no-ops. */
#define timeBeginPeriod(p)  (0)
#define timeEndPeriod(p)    (0)

/* ---- functions implemented in tiger_plat_posix.c ----------------------- */
/* Memory */
void  *VirtualAlloc(void *addr, size_t size, DWORD type, DWORD protect);
BOOL   VirtualFree(void *addr, size_t size, DWORD type);

/* Critical sections (recursive mutexes) */
void   InitializeCriticalSection(CRITICAL_SECTION *cs);
void   EnterCriticalSection(CRITICAL_SECTION *cs);
void   LeaveCriticalSection(CRITICAL_SECTION *cs);
void   DeleteCriticalSection(CRITICAL_SECTION *cs);
BOOL   TryEnterCriticalSection(CRITICAL_SECTION *cs);

/* Condition variables (paired with a CRITICAL_SECTION) */
void   InitializeConditionVariable(CONDITION_VARIABLE *cv);
BOOL   SleepConditionVariableCS(CONDITION_VARIABLE *cv, CRITICAL_SECTION *cs, DWORD ms);
void   WakeConditionVariable(CONDITION_VARIABLE *cv);
void   WakeAllConditionVariable(CONDITION_VARIABLE *cv);

/* Waitable handles: threads, events and semaphores share one object so that
 * WaitForSingleObject and CloseHandle dispatch across all three. */
typedef DWORD (*plat_thread_fn)(void *);
HANDLE CreateThread(void *sec, size_t stack, plat_thread_fn fn, void *arg,
                    DWORD flags, DWORD *tid);
HANDLE CreateEventA(void *sec, BOOL manual, BOOL initial, const char *name);
HANDLE OpenEventA(DWORD access, BOOL inherit, const char *name);
BOOL   SetEvent(HANDLE h);
BOOL   ResetEvent(HANDLE h);
HANDLE CreateSemaphoreA(void *sec, long initial, long maximum, const char *name);
BOOL   ReleaseSemaphore(HANDLE h, long count, long *previous);
DWORD  WaitForSingleObject(HANDLE h, DWORD ms);
BOOL   CloseHandle(HANDLE h);
#define CreateEvent     CreateEventA
#define CreateSemaphore CreateSemaphoreA

/* Thread-local storage */
DWORD  TlsAlloc(void);
BOOL   TlsFree(DWORD index);
void  *TlsGetValue(DWORD index);
BOOL   TlsSetValue(DWORD index, void *value);
DWORD  GetCurrentThreadId(void);

/* Timing */
DWORD              GetTickCount(void);
unsigned long long GetTickCount64(void);
BOOL   QueryPerformanceCounter(LARGE_INTEGER *count);
BOOL   QueryPerformanceFrequency(LARGE_INTEGER *freq);
void   GetSystemTimeAsFileTime(FILETIME *ft);
void   Sleep(DWORD ms);

/* Files and mappings */
HANDLE _get_osfhandle(int fd);
int    _open_osfhandle(intptr_t h, int flags);
HANDLE CreateFileA(const char *path, DWORD access, DWORD share, void *sec,
                   DWORD disposition, DWORD flags, HANDLE tmpl);
BOOL   GetFileInformationByHandle(HANDLE h, BY_HANDLE_FILE_INFORMATION *info);
BOOL   ReadFile(HANDLE h, void *buf, DWORD n, DWORD *got, OVERLAPPED *ov);
DWORD  GetLastError(void);
void   SetLastError(DWORD err);
HANDLE CreateFileMappingA(HANDLE fh, void *sec, DWORD protect,
                          DWORD sizeHigh, DWORD sizeLow, const char *name);
void  *MapViewOfFile(HANDLE mh, DWORD access, DWORD offHigh, DWORD offLow,
                     size_t nbytes);
BOOL   UnmapViewOfFile(void *base);
void   GetSystemInfo(SYSTEM_INFO *si);
DWORD  GetModuleFileNameA(HMODULE mod, char *buf, DWORD size);
#define CreateFileMapping CreateFileMappingA

/* Dynamic loader */
HMODULE LoadLibraryA(const char *name);
void   *GetProcAddress(HMODULE h, const char *name);
BOOL    FreeLibrary(HMODULE h);
#define LoadLibrary LoadLibraryA

#endif /* TIGER_PLAT_H */
