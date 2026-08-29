/* tiger_host_api.c -- the same host, as a DLL.
 *
 * Part of tiger_host.c, which includes it when PT_DLL is defined; see there
 * for why this is one translation unit.
 *
 * **Why a DLL exists at all.**  NVDA refuses to copy an `.exe` into
 * `systemConfig` -- `config._setSystemConfig` drops every file ending `.exe`,
 * deliberately and silently -- so an add-on that ships one has no engine on
 * the sign-in desktop, on a UAC prompt, or on any other secure screen.  The
 * user has chosen this synthesizer and is handed a different one at exactly
 * the moment a password is being typed.  A `.dll` is copied like any other
 * file, and NVDA's own 32-bit bridge gives a 64-bit NVDA a 32-bit process to
 * load it into -- which is the other half of what this program needs, since
 * Apple's MacinTalk is i386 code and always will be.
 *
 * **What is not here is the point.**  There is no second protocol, no pull
 * API, no reimplementation of the request loop.  `serve` is the same function
 * the executable runs, reading and writing the same bytes in the same order;
 * all that changes is which two `FILE *` it has.  A response from this DLL is
 * expected to be byte-identical to a response from the executable, and the
 * smoke test compares them rather than assuming it.
 *
 * The two pipes are private ones made here.  Redirecting the process's own
 * stdin and stdout -- the obvious way to get `serve` running untouched -- is
 * the one thing that must never happen: inside NVDA's bridge host those carry
 * the RPyC connection to NVDA, and taking them would cut the driver off from
 * the screen reader that loaded it.
 */

/* Both directions, from the caller's point of view: `request` is written by
 * the caller and read by `serve`, `response` the other way about. */
static HANDLE g_req_w, g_rsp_r;         /* the caller's ends, handed out */
static HANDLE g_thread;
static HANDLE g_ready;                  /* set when open has succeeded or not */
static int    g_open_err = -1;          /* what it was, once g_ready is set */
static char   g_mtpath[CFPATH], g_sdpath[CFPATH], g_voicesdir[CFPATH];

/* The bring-up and then the request loop, on one thread for the whole life of
 * the DLL.
 *
 * One thread on purpose, and the same one throughout: the executable maps the
 * images, opens the channel and serves all on its main thread, and enough of
 * what runs underneath -- the GCD shims, the audio graph, the engine's own
 * worker handoffs -- has been debugged only in that arrangement.  Splitting
 * bring-up from serving across two threads would be a difference from the
 * measured program for no gain at all.
 */
static DWORD WINAPI host_thread(void *unused)
{
    (void)unused;
    g_open_err = host_open(g_mtpath, g_sdpath);
    SetEvent(g_ready);
    if (g_open_err) return (DWORD)g_open_err;
    fprintf(stderr, "tiger_host: ready, voices in %s\n", g_voicesdir);
    return (DWORD)serve(&g_mt, g_chan, g_voicesdir);
}

/* Open the engine and return the two pipe ends to talk to it through.
 *
 * `cancelname` is the name of the Windows event the driver signals to abandon
 * an utterance, or NULL.  It is passed rather than opened here because `serve`
 * already reads it from `TIGER_CANCEL_EVENT` and opens it itself; putting it
 * in the environment keeps that one line of code, and one code path, for both
 * builds.
 *
 * Returns 0, or the OSErr the engine gave -- so a caller can tell "no engine
 * at that path" from "the channel would not open" the same way the executable
 * does.  **Once per process**: `host_open` has no inverse, and a second engine
 * in one 2 GB address space would not fit beside Alex in any case.
 */
__declspec(dllexport) int __cdecl pt_open(const char *mtpath,
                                          const char *sdpath,
                                          const char *voicesdir,
                                          const char *cancelname,
                                          HANDLE *reqwrite,
                                          HANDLE *rspread)
{
    HANDLE reqr = NULL, rspw = NULL;
    int infd, outfd;

    if (g_thread) return -2;            /* already open; see above */
    if (!mtpath || !sdpath || !voicesdir || !reqwrite || !rspread) return -3;

    host_quiet();

    _snprintf(g_mtpath, sizeof(g_mtpath), "%s", mtpath);
    _snprintf(g_sdpath, sizeof(g_sdpath), "%s", sdpath);
    _snprintf(g_voicesdir, sizeof(g_voicesdir), "%s", voicesdir);
    g_mtpath[sizeof(g_mtpath) - 1] = 0;
    g_sdpath[sizeof(g_sdpath) - 1] = 0;
    g_voicesdir[sizeof(g_voicesdir) - 1] = 0;

    /* `_putenv_s`, never `SetEnvironmentVariableA`.  The two are not the same
     * environment: the CRT snapshots the process block at startup and `getenv`
     * reads that snapshot, so a Win32 set would be invisible to the one line
     * that has to see it -- `serve`'s own `getenv("TIGER_CANCEL_EVENT")` --
     * and every interrupted utterance would quietly render to the end. */
    if (cancelname && *cancelname)
        _putenv_s("TIGER_CANCEL_EVENT", cancelname);

    /* Inheritable handles would be wrong twice over: nothing is spawned from
     * here, and the bridge host is itself a child that should not be handing
     * copies of these to anything it starts. */
    if (!CreatePipe(&reqr, &g_req_w, NULL, 0)) return -4;
    if (!CreatePipe(&g_rsp_r, &rspw, NULL, 0)) {
        CloseHandle(reqr); CloseHandle(g_req_w); g_req_w = NULL;
        return -4;
    }

    /* `serve` wants stdio streams, so the two ends it reads and writes become
     * `FILE *` here.  _fdopen takes ownership of the fd, and the fd owns the
     * handle: closing g_in and g_out at the end closes both. */
    infd  = _open_osfhandle((intptr_t)reqr, _O_RDONLY | _O_BINARY);
    outfd = _open_osfhandle((intptr_t)rspw, _O_BINARY);
    if (infd < 0 || outfd < 0) return -5;
    g_in  = _fdopen(infd, "rb");
    g_out = _fdopen(outfd, "wb");
    if (!g_in || !g_out) return -5;

    g_ready = CreateEventA(NULL, TRUE, FALSE, NULL);
    if (!g_ready) return -6;
    g_thread = CreateThread(NULL, 0, host_thread, NULL, 0, NULL);
    if (!g_thread) return -6;

    /* Block until the engine is up, so that a caller holding a zero return
     * knows the next thing it writes will be read.
     *
     * The driver's whole interrupt storm was a standby host that was alive but
     * not ready, and this is the same distinction: `pt_open` returning 0 *is*
     * readiness, which is why nothing here reproduces the executable's
     * "tiger_host: ready," line for anyone to watch for. */
    WaitForSingleObject(g_ready, INFINITE);
    if (g_open_err) return g_open_err;

    *reqwrite = g_req_w;
    *rspread  = g_rsp_r;
    return 0;
}

/* Let go.
 *
 * **The caller closes its own two ends first, and this closes only the pair it
 * kept.**  The handles returned by `pt_open` belong to the caller from the
 * moment they are returned -- Python wraps them in file objects that close
 * them -- so closing them here as well would be closing a handle twice, and
 * the second close in a process this size lands on whatever has since been
 * given the same number.
 *
 * With the request end shut, `serve` reads end-of-file and returns, exactly as
 * it does when the driver's process goes away.  A render already in flight is
 * the case worth being careful about: it is blocked writing frames nobody will
 * read, so the cancel event is signalled first to give it the same quick exit
 * an interrupted utterance gets.
 *
 * Nothing is unmapped -- `host_open` has no inverse -- and nothing needs to
 * be: NVDA's bridge starts a fresh host process for each synthesizer and kills
 * it through a job object when the driver goes, so the process is the
 * teardown.  That is also why a timeout here is not a leak worth chasing.
 */
__declspec(dllexport) void __cdecl pt_close(void)
{
    if (!g_thread) return;
    if (g_cancel_ev) SetEvent(g_cancel_ev);
    /* A second is far longer than any exit path measured here, and the point
     * of having a bound at all is that a wedged engine must not be able to
     * keep NVDA from shutting down. */
    if (WaitForSingleObject(g_thread, 1000) == WAIT_OBJECT_0) {
        /* Only once the thread is certainly gone.  These two are the ends
         * `serve` itself reads and writes, and closing them under a live
         * `serve` would be the same double-close by another route. */
        if (g_in)  { fclose(g_in);  g_in  = NULL; }
        if (g_out) { fclose(g_out); g_out = NULL; }
    }
    CloseHandle(g_thread);
    g_thread = NULL;
}
