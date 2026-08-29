# -*- coding: utf-8 -*-
"""The engine as a library, wearing the shape of a process.

**Why this exists.**  NVDA drops every file ending `.exe` when it copies the
user configuration to `systemConfig` -- deliberately, and silently -- so on the
sign-in desktop, on a UAC prompt, and on every other secure screen there is no
`panthera_host.exe` to start.  The user has chosen this synthesizer and is
handed a different one at exactly the moment a password is being typed.  A
`.dll` is copied like any other file, and NVDA's own 32-bit bridge supplies a
32-bit process to load it into, which is the other half of what Apple's i386
MacinTalk needs.

**Why it is shaped like `Popen` rather than like a library.**  Everything the
driver knows about talking to an engine -- the wire format, the streaming
reader, the chunk handling, the cancel event, retiring a host whose utterance
nobody will hear -- is written against a process, and all of it is right.  A
grep for what `host.py` actually touches on one comes to seven names:

    kill  pantheraReady  pid  poll  stderr  stdin  stdout  wait

That is a small enough surface to wear, and wearing it means `_render` and the
protocol keep working unchanged, which is the same reason `serve` in the C is
one function for both builds.  The alternative was a second code path through
the most heavily measured part of this driver, to no benefit.

**What is genuinely different, and is not hidden.**  There is one engine per
process and no way to unmap it, so there is no standby host and no second host
to hand off to -- `HostMixin` asks before doing either.  A wedged session
cannot be killed the way a wedged process can; NVDA's bridge kills the whole
host process when the driver goes, and that is the real teardown.
"""
import ctypes
import msvcrt
import os
import threading

from logHandler import log

#: The DLL, its log pump, and the session count -- all per process, because
#: the engine inside it is.
_dll = None
_logStream = None
_lock = threading.Lock()


class _PtError(RuntimeError):
    """`pt_open` refused, with the code it refused by."""


def available(hostDll):
    """-> True if this process could actually load that DLL.

    Bitness is the whole question.  Apple's MacinTalk is i386 code, so the
    engine can only be called from a 32-bit process; in 64-bit NVDA the answer
    is no, and the driver module puts a bridge proxy in its place instead.
    """
    return (ctypes.sizeof(ctypes.c_void_p) == 4
            and os.path.isfile(hostDll))


def haveHost(hostExe, hostDll):
    """-> True when this add-on could actually speak, here, as things stand.

    This is what decides whether a generation is offered in the synthesizer
    list, so it has to be the *whole* question rather than "is there a file":

    * an executable can be spawned from any process, so our bitness does not
      enter into it;
    * a library can be loaded here only if we are 32-bit;
    * and a library in 64-bit NVDA is still usable, but only through NVDA's own
      32-bit bridge -- which older NVDA does not have.

    Gating this on the executable alone is how the whole DLL could have been
    finished and still changed nothing: every generation would have reported
    itself unusable on every secure screen, the placeholder would have taken
    their place, and the engine sitting right there would never have been
    asked.  Gating it on either *file* rather than on this would have been the
    opposite mistake -- claiming a synthesizer that then throws on its first
    word, which is how the driver behaved for one commit.
    """
    if os.path.isfile(hostExe):
        return True
    if not os.path.isfile(hostDll):
        return False
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        return True
    from . import bridge
    return bridge.available()


def useLibrary(hostExe, hostDll):
    """-> True when the engine has to be a library rather than a process.

    **The question is whether the executable is beside us, and that one fact
    settles everything.**  On an ordinary desktop it is, and the driver goes on
    doing exactly what it has always done.  In `systemConfig` -- the copy NVDA
    speaks from on the sign-in desktop, on a UAC prompt and on every other
    secure screen -- it is not, because `config._setSystemConfig` drops every
    file ending `.exe`.  There the engine is the DLL beside it instead.

    Asking about the file rather than about the screen is deliberate.  A secure
    screen is not really the condition; *not having an executable* is, and the
    two coincide only by NVDA's current rule.  This way an add-on stripped of
    its executable for any other reason still speaks, and nothing here has to
    be kept in step with how NVDA decides what a secure screen is.

    **It lives here rather than on the driver because there are two drivers.**
    Tiger has its own, written before the shared body existed, and a rule about
    when to use the library that each of them answered separately would be a
    rule they could answer differently -- on the one screen nobody can attach a
    debugger to.
    """
    return not os.path.isfile(hostExe) and available(hostDll)


def _load(hostDll, name):
    """The DLL, loaded once, with its log already being drained.

    **The log pipe is opened and read before anything can write to it, and
    that is an ordering requirement rather than tidiness.**  Bringing the
    engine up writes more than a pipe holds; with no reader yet the DLL blocks
    inside an `fprintf` half way through mapping the images, and `pt_open`
    never returns.  That deadlock was built once, during this work, which is
    why `pt_logpipe` is a separate entry point at all.
    """
    global _dll, _logStream
    if _dll is not None:
        return _dll
    dll = ctypes.CDLL(hostDll)
    dll.pt_logpipe.restype = ctypes.c_int
    dll.pt_logpipe.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    dll.pt_open.restype = ctypes.c_int
    dll.pt_open.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                            ctypes.c_char_p, ctypes.POINTER(ctypes.c_char_p),
                            ctypes.POINTER(ctypes.c_void_p),
                            ctypes.POINTER(ctypes.c_void_p)]
    dll.pt_alive.restype = ctypes.c_int
    dll.pt_alive.argtypes = []
    dll.pt_close.restype = None
    dll.pt_close.argtypes = []

    handle = ctypes.c_void_p()
    if dll.pt_logpipe(ctypes.byref(handle)):
        raise _PtError("the engine library would not open its log")
    _logStream = os.fdopen(
        msvcrt.open_osfhandle(handle.value, os.O_RDONLY), "rb")
    # **Started here, before the first `pt_open`, and that is the whole
    # point.**  Bringing the engine up writes more than a pipe holds, and if
    # the engine gives up it says why and then ends the process -- so a reader
    # that only starts once `pt_open` has returned is a reader that can neither
    # prevent the block nor catch the explanation.  Both were observed: a
    # deadlock while this was being written, and later a bring-up that failed
    # with nothing in the log at all, because the one line saying why was
    # sitting unread in this pipe.
    thread = threading.Thread(target=_pump, args=(name,),
                              name="panthera-library-log")
    thread.daemon = True
    thread.start()
    _dll = dll
    return _dll


#: Said once per host and several hundred lines long, so it belongs at debug.
#: The same list as `host.py`'s, and for the same reason: a user's log filled
#: with loader commentary buries the one line that matters.
_ROUTINE = ("ready,", "session ready", "verbose logging on",
            "reading engine parameters", "parameter ")


def _pump(name):
    """Put everything the library says into NVDA's log, forever.

    It never ends, because the library holds the writing end of this pipe for
    as long as the process lives.  A daemon thread, so that costs nothing at
    shutdown.
    """
    try:
        for raw in iter(_logStream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            if not line.startswith("tiger_host:"):
                log.debug("%s library: %s" % (name, line))
            elif line[11:].lstrip().startswith(_ROUTINE):
                log.debug("%s library: %s" % (name, line))
            else:
                log.warning("%s library: %s" % (name, line))
    except Exception:
        pass


class DllHost(object):
    """One session with the engine library, answering to `Popen`'s questions."""

    def __init__(self, name, hostDll, mt, sd, voicesdir, cancelName, env):
        with _lock:
            dll = _load(hostDll, name)
            block = (ctypes.c_char_p * (len(env) + 1))()
            for i, entry in enumerate(env):
                block[i] = entry.encode("mbcs")
            reqw, rspr = ctypes.c_void_p(), ctypes.c_void_p()
            err = dll.pt_open(
                mt.encode("mbcs"), sd.encode("mbcs"),
                voicesdir.encode("mbcs"),
                cancelName.encode("mbcs") if cancelName else None,
                block, ctypes.byref(reqw), ctypes.byref(rspr))
            if err:
                raise _PtError("the engine library refused to open (%d)" % err)
            self.stdin = os.fdopen(msvcrt.open_osfhandle(reqw.value, 0), "wb")
            self.stdout = os.fdopen(
                msvcrt.open_osfhandle(rspr.value, os.O_RDONLY), "rb")
            #: **Always None, and not an oversight.**  The library's log
            #: belongs to the process, not to a session, and it is already
            #: being read -- `_load` starts that reader before the engine can
            #: write a word.  Handing the same pipe to `_watchStderr` as well
            #: would put two readers on it, taking turns and splitting lines
            #: between them; both drivers check for None and stand aside.
            self.stderr = None
            self._closed = False

        #: Alive is not ready, and the difference was the whole interrupt
        #: storm -- but here there is nothing to wait for.  `pt_open` does not
        #: return until the engine is up, so a session that exists is a session
        #: that can answer, and the flag is set before anybody looks.
        self.pantheraReady = threading.Event()
        self.pantheraReady.set()
        #: There is no child, so this is the honest answer to "which process
        #: is the engine in": this one.
        self.pid = os.getpid()

    def poll(self):
        """None while the session can still answer, 0 once it cannot."""
        if self._closed:
            return 0
        return None if _dll.pt_alive() else 0

    def kill(self):
        self._close()

    def terminate(self):
        self._close()

    def wait(self, timeout=None):
        self._close()
        return 0

    def _close(self):
        """Let go, in the order the library asks for.

        Our ends first: the handles `pt_open` returned belong to this object,
        and the DLL deliberately does not touch them, so that no handle is ever
        closed twice.  Closing the request end is also what tells `serve` to
        return, exactly as a dead driver process does.
        """
        with _lock:
            if self._closed:
                return
            self._closed = True
            for stream in (self.stdin, self.stdout):
                try:
                    stream.close()
                except Exception:
                    pass
            try:
                _dll.pt_close()
            except Exception:
                log.debugWarning("panthera: the engine library would not "
                                 "close cleanly", exc_info=True)
