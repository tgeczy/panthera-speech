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
 * expected to be identical to a response from the executable, and
 * `tools/dll_smoke.py` compares them rather than assuming it.
 *
 * ---- Two lifetimes, and the difference is the whole design ---------------
 *
 * **The process** maps the images, binds them and runs their initialisers.
 * That happens once and cannot be undone: the loader has no teardown path, and
 * a second copy of Leopard's Alex would not fit in a 2 GB address space beside
 * the first.
 *
 * **A session** is a speech channel, a pair of pipes and a `serve` loop.  All
 * of it is cheap and all of it is repeatable -- reopening the channel was
 * measured at 1 to 5 ms, Alex included -- so `pt_open` may be called again
 * after `pt_close`, and the second call skips the engine and rebuilds only the
 * session.
 *
 * That split is what lets the driver keep the code it already has.  Its answer
 * to "the phrasing setting changed" and to "the inflection slider came home"
 * is `_restartHost`, and with sessions that keeps meaning exactly what it
 * meant: the settings are re-read, the channel is new, and a new channel has
 * no `[[pmod]]` on it -- which is what `--pmod-check` was written to prove.
 *
 * The two pipes are private ones made here.  Redirecting the process's own
 * stdin and stdout -- the obvious way to get `serve` running untouched -- is
 * the one thing that must never happen: inside NVDA's bridge host those carry
 * the RPyC connection to NVDA, and taking them would cut the driver off from
 * the screen reader that loaded it.
 */

/* Both directions, from the caller's point of view: `request` is written by
 * the caller and read by `serve`, `response` and `log` the other way about. */
static HANDLE g_req_w, g_rsp_r, g_log_r;    /* the caller's ends, handed out */
static HANDLE g_thread;
static HANDLE g_ready;                  /* set when a session is up, or is not */
static int    g_open_err = -1;          /* what it was, once g_ready is set */
static int    g_engine_up;              /* host_open has run and succeeded */
static int    g_wedged;                 /* a session would not end; see pt_close */
static char   g_mtpath[CFPATH], g_sdpath[CFPATH], g_voicesdir[CFPATH];

/* A second and later session: re-read what used to arrive with a new process,
 * and put a fresh channel under `serve`.
 *
 * `g_nparams = 0` is not tidiness.  `cf_params_init` **appends** to the
 * parameter table and the lookup returns the first key that matches, so
 * running it twice without this would leave the *old* phrasing winning over
 * the new one -- a settings change that silently did nothing, which is the
 * shape of half the bugs this project has had.  The `cfobj`s the previous pass
 * allocated are abandoned; they are a few dozen small objects per settings
 * change, and freeing them would mean knowing that the engine has let go of
 * them, which nothing here can know.
 *
 * `serve`'s caches need no attention, and that is worth saying out loud
 * because it looks like an omission: `curvoice`, `currate` and `curpitch` are
 * locals, so a new call to `serve` starts with all three empty and restates
 * everything on the first request.  A fresh channel with stale caches would be
 * the same bug as the interrupted-utterance one -- speech at the engine's own
 * 180 wpm -- and the structure rules it out rather than remembering to. */
static int host_session(void)
{
    SEClose_t closechan = (SEClose_t)find_export(&g_mt,
                                                 "_SECloseSpeechChannel");
    SEOpenChan_t openchan = (SEOpenChan_t)find_export(&g_mt,
                                                      "_SEOpenSpeechChannel");
    void *fresh = NULL;
    int err;

    g_no_abbrev = getenv("TIGER_NO_ABBREV") ? 1 : 0;
    g_pref_log  = getenv("TIGER_PREF_LOG") ? 1 : 0;
    g_gcd_log   = getenv("TIGER_GCD_LOG") ? 1 : 0;
    g_nparams = 0;
    cf_params_init();
    /* Say what this session is, both ways round.
     *
     * `host_open` announces only the "off" case, and for a process that is
     * enough -- it is a whole new environment and there is nothing it could be
     * confused with.  A session is different: it exists *because* a setting
     * changed, so the interesting thing is that the new value arrived, and a
     * state reported only when it is off can only ever prove half of that.
     *
     * It is what the smoke test asserts on, too, and deliberately so.  Whether
     * turning these rules off changes any given sentence depends on the
     * generation and on the words; whether the flag reached the engine does
     * not.  Testing the audio instead sent one afternoon looking for a bug in
     * the DLL that was really a sentence Tiger reads the same way either
     * way. */
    fprintf(stderr, "tiger_host: session ready, abbreviation rules %s\n",
            g_no_abbrev ? "OFF" : "on");

    if (!openchan) return -1;
    if (g_chan && closechan) call_aligned1((void *)closechan, g_chan);
    g_chan = NULL;
    err = call_aligned1((void *)openchan, &fresh);
    if (err) return err;
    if (!fresh) return -1;
    g_chan = fresh;
    return 0;
}

/* The bring-up and then the request loop, on one thread for the whole life of
 * a session.
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
    if (!g_engine_up) {
        g_open_err = host_open(g_mtpath, g_sdpath);
        g_engine_up = (g_open_err == 0);
    } else {
        g_open_err = host_session();
    }
    SetEvent(g_ready);
    if (g_open_err) return (DWORD)g_open_err;
    fprintf(stderr, "tiger_host: ready, voices in %s\n", g_voicesdir);
    return (DWORD)serve(&g_mt, g_chan, g_voicesdir);
}

/* Send this module's own diagnostics down a pipe instead of to the console.
 *
 * **`/MT` is what makes this safe.**  A statically linked CRT gives this DLL
 * its own file-descriptor table, so `_dup2` onto fd 2 moves *this module's*
 * stderr and nothing else -- the bridge host's own streams, which carry RPyC
 * to NVDA, are untouched.  A `/MD` build would share the CRT and this would
 * redirect NVDA's host as well.
 *
 * The diagnostics have to go somewhere a user can send: Vicki's AAC decoding
 * is done by whatever decoder that copy of Windows ships, and one that behaves
 * differently makes her sound wrong rather than silent.  The host says so on
 * stderr, and this is what gets that into NVDA's log from inside the bridge.
 *
 * It is also not optional in the other direction: a pipe nobody reads fills
 * up, and then this module blocks inside an `fprintf` and the screen reader
 * goes quiet.  The driver's `_watchStderr` thread is what stops that, and it
 * is the same thread that has always done it for the executable.
 *
 * **It must be opened, and drained, before `pt_open` is called.**  That is not
 * a preference.  A pipe holds 64 KB; bringing the engine up writes more than
 * that when the loader is talking, and there is no reader yet because
 * `pt_open` has not returned the handle -- so the DLL blocks inside an
 * `fprintf` in the middle of `host_open`, `pt_open` never returns, and the
 * caller waits for a thread that is waiting for the caller.  That deadlock was
 * built once, here, before the pipe was given its own entry point.
 *
 * So it is separate, and it is per *process* rather than per session: fd 2
 * belongs to the module, not to a speech channel, and one reader for the life
 * of the DLL is what matches that.  Calling it twice returns the same pipe.
 */
__declspec(dllexport) int __cdecl pt_logpipe(HANDLE *readend)
{
    HANDLE w = NULL;
    int fd;

    if (!readend) return -3;
    if (g_log_r) { *readend = g_log_r; return 0; }
    if (!CreatePipe(&g_log_r, &w, NULL, 0)) return -4;
    fd = _open_osfhandle((intptr_t)w, _O_BINARY);
    if (fd < 0) { CloseHandle(w); CloseHandle(g_log_r); g_log_r = NULL;
                  return -4; }
    /* Onto fd 2 itself rather than reopening `stderr`, so that the 88
     * `printf`s redirected to stderr at the top of tiger_host.c travel with
     * it.  This also closes whatever fd 2 held before, which is the console. */
    _dup2(fd, 2);
    _close(fd);
    setvbuf(stderr, NULL, _IONBF, 0);
    *readend = g_log_r;
    return 0;
}

/* Open a session and return the two pipe ends to talk to it through.
 *
 * Call `pt_logpipe` first and start reading what it returns; see there for why
 * that is an ordering requirement and not a suggestion.
 *
 * `env` is a NULL-terminated array of "NAME=VALUE" strings, or NULL.  It
 * exists because **`os.environ` in the caller cannot reach `getenv` here**:
 * Python's `putenv` updates the Win32 environment, and this module's `/MT` CRT
 * snapshots its own copy when it is loaded and never looks at the Win32 block
 * again.  So every `TIGER_*` setting the driver used to deliver by starting a
 * new process with a new environment has to come through here instead.  An
 * entry with an empty value removes the variable, which is how a flag is
 * turned off.
 *
 * `cancelname` is the Windows event the driver signals to abandon an
 * utterance, or NULL.  It goes into the environment with the rest, because
 * `serve` already reads it from `TIGER_CANCEL_EVENT` and opens it itself --
 * which keeps one code path for both builds.
 *
 * Returns 0, or the OSErr the engine gave, or a negative code of this file's
 * own.  Callable again after `pt_close`; the engine is brought up only on the
 * first call.
 */
__declspec(dllexport) int __cdecl pt_open(const char *mtpath,
                                          const char *sdpath,
                                          const char *voicesdir,
                                          const char *cancelname,
                                          const char *const *env,
                                          HANDLE *reqwrite,
                                          HANDLE *rspread)
{
    HANDLE reqr = NULL, rspw = NULL;
    int infd, outfd;

    if (g_wedged) return -8;            /* see pt_close */
    if (g_thread) return -2;            /* a session is already open */
    if (!mtpath || !sdpath || !voicesdir || !reqwrite || !rspread) return -3;

    _snprintf(g_mtpath, sizeof(g_mtpath), "%s", mtpath);
    _snprintf(g_sdpath, sizeof(g_sdpath), "%s", sdpath);
    _snprintf(g_voicesdir, sizeof(g_voicesdir), "%s", voicesdir);
    g_mtpath[sizeof(g_mtpath) - 1] = 0;
    g_sdpath[sizeof(g_sdpath) - 1] = 0;
    g_voicesdir[sizeof(g_voicesdir) - 1] = 0;

    /* `_putenv_s`, never `SetEnvironmentVariableA`.  The two are not the same
     * environment, and only this one is the one `getenv` reads. */
    for (; env && *env; env++) {
        const char *eq = strchr(*env, '=');
        char name[128];
        size_t n;
        if (!eq) continue;
        n = (size_t)(eq - *env);
        if (n == 0 || n >= sizeof(name)) continue;
        memcpy(name, *env, n);
        name[n] = 0;
        _putenv_s(name, eq + 1);        /* an empty value removes it */
    }
    if (cancelname && *cancelname)
        _putenv_s("TIGER_CANCEL_EVENT", cancelname);

    /* After the environment, because that is where TIGER_HOST_VERBOSE arrives,
     * and before anything is printed.  Without it the loader's several hundred
     * lines of commentary go into NVDA's log on every start. */
    host_quiet();

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

    if (!g_ready) g_ready = CreateEventA(NULL, TRUE, FALSE, NULL);
    else ResetEvent(g_ready);
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

/* Is the session still able to answer?  1 while `serve` is running.
 *
 * A process either exists or it does not, and `Popen.poll` says which; a
 * session has no such thing to ask, so this stands in for it.  `serve` returns
 * on its own if a request does not begin with a magic it knows -- the
 * protocol-desync exit -- and without this the driver would keep writing into
 * a session nobody is reading and wait for a response nobody will send.  The
 * executable makes that visible by dying; here it has to be asked.
 */
__declspec(dllexport) int __cdecl pt_alive(void)
{
    return g_thread &&
           WaitForSingleObject(g_thread, 0) == WAIT_TIMEOUT;
}

/* End the session.
 *
 * **The caller closes its own three ends first, and this closes only the pair
 * it kept.**  The handles returned by `pt_open` belong to the caller from the
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
 * **A session that will not end is permanent.**  If the join times out, the
 * old thread still owns `g_in` and `g_out`, and a later `pt_open` that
 * overwrote them would be handing a live thread a freed stream -- so the DLL
 * refuses to open again instead.  In the bridge that costs the user this
 * synthesizer until they switch away and back, which kills the host process
 * and takes the wedge with it; that job-object kill is the real teardown here
 * and always was.  `CancelSynchronousIo` is the tool if this is ever observed
 * rather than merely guarded against.
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
    } else {
        g_wedged = 1;
        fprintf(stderr, "tiger_host: the engine did not stop; this process "
                        "cannot serve again\n");
    }
    CloseHandle(g_thread);
    g_thread = NULL;
    /* Not g_log_r: the log pipe outlives every session, because fd 2 belongs
     * to the module rather than to a speech channel. */
    g_req_w = g_rsp_r = NULL;
}
