# -*- coding: utf-8 -*-
"""Starting an engine process, talking to it, and taking it away again.

Split out of `pantheradriver.py` unchanged.  This is the whole of the driver's
relationship with `panthera_host.exe`: the wire format, spawning, the standby
host, the cancel event, draining stderr, retiring a host an utterance nobody
will hear still owns, and `_render` -- the one call that turns text into PCM.

**A plain mixin, deliberately.**  NVDA builds a driver's settings properties
from `_get_`/`_set_` names found in the class's *own* body, so accessors
cannot be moved to a mixin without care; nothing here is an accessor, so
nothing here is at risk.  `HANDOFF_GRACE` and `ABANDON_GRACE` are ordinary
class attributes and a generation still overrides them by declaring its own.

It is one file because it is one subject, and because it is the file that
changes if the host ever stops being a separate process: the add-on is dead on
NVDA's secure screens for as long as it ships an `.exe`, since NVDA drops
every executable when it copies the configuration to `systemConfig`.  Whatever
replaces that -- a DLL loaded in-process, NVDA's own 32-bit bridge -- rewrites
this module and touches little else.  That was worth arranging before it was
worth doing.
"""
import os
import struct
import subprocess
import threading
import time

from logHandler import log

from . import dllhost
from . import pantheraabbrev
from . import pantheranumbers
from . import pantherastress
from .constants import INFLECTION_MAX_PMOD, volume_volm
from .text import (COMMAND_RE, COMMAND_SPLIT_RE, INPUT_MODE_CAPTURE_RE,
                   INPUT_MODE_RE, _encode)

REQ_MAGIC = 0x54475233          # 'TGR3'
#: The same audio, in chunks, as the engine produces it.
#:
#: A separate magic rather than a flag so that a stale `tiger_host.exe` left in
#: an add-on folder refuses the request outright instead of answering it in a
#: shape this driver would read as chunk lengths.
REQ_MAGIC_STREAM = 0x54475234   # 'TGR4'
RSP_MAGIC = 0x54475253          # 'TGRS'


def _readExactly(stream, n):
    """Read exactly `n` bytes or raise.  A pipe read can always come up short."""
    out = b""
    while len(out) < n:
        chunk = stream.read(n - len(out))
        if not chunk:
            raise IOError("engine closed the pipe")
        out += chunk
    return out


#: reason they were ever loud is that everything the host prefixes with its
#: own name was treated as one.  A real complaint -- a voice that will not
#: decode, a tree it cannot read -- is not in this list and stays at warning.
_HOST_ROUTINE = (
    "ready,",
    "verbose logging on",
    "reading engine parameters",
    "parameter ",
)


class HostMixin(object):
    """The host process, and everything said down the pipe to it."""

    # -- the host ----------------------------------------------------------
    def _useLibrary(self):
        """-> True when the engine has to be a library rather than a process.

        The rule itself is `dllhost.useLibrary`, which Tiger's separate driver
        asks as well; see there for why it is one question in one place.
        """
        return dllhost.useLibrary(self.TREE.HOST_EXE, self.TREE.HOST_DLL)

    def _startHost(self, standby=False):
        """Start one engine process and return it without choosing it.

        Callers hold ``_procLock`` while choosing whether the result is the
        active or standby host.  Keeping process construction separate from
        that choice is what lets cancellation overlap a replacement start
        with the grace already being given to the old response.
        """
        if self._stopped:
            # `terminate()` raises this flag before taking `_procLock`.
            # Without the check, a retirement already in flight could leave a
            # process running with nothing to serve.
            raise RuntimeError("%s is shutting down" % self.name)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        # Follow NVDA's own log level. Someone who has turned debug logging on
        # has asked for detail, and the host is the only view of the engine.
        env = dict(os.environ)
        try:
            import logging
            if log.isEnabledFor(logging.DEBUG):
                env["TIGER_HOST_VERBOSE"] = "1"
            else:
                env.pop("TIGER_HOST_VERBOSE", None)
        except Exception:
            env.pop("TIGER_HOST_VERBOSE", None)
        if self._cancelEvent:
            env["TIGER_CANCEL_EVENT"] = self._cancelEventName
        params = self._phrasingParam()
        if params:
            env["TIGER_PARAMS"] = params
        else:
            env.pop("TIGER_PARAMS", None)
        if self._expandAbbreviations:
            env.pop("TIGER_NO_ABBREV", None)
        else:
            env["TIGER_NO_ABBREV"] = "1"

        if self._useLibrary():
            # No executable to start, so the engine is a library in this
            # process instead.  Everything below is the same afterwards: what
            # comes back answers `Popen`'s questions and carries the same two
            # pipes, so the protocol, the streaming reader and the cancel
            # event never learn which one they got.
            proc = dllhost.DllHost(
                self.name, self.TREE.HOST_DLL, self._mt, self._sd, self._voicesdir,
                self._cancelEventName if self._cancelEvent else None,
                ["%s=%s" % (k, env.get(k, ""))
                 for k in ("TIGER_HOST_VERBOSE", "TIGER_PARAMS",
                           "TIGER_NO_ABBREV")])
        else:
            proc = subprocess.Popen(
                [self.TREE.HOST_EXE, "--serve", self._mt, self._sd,
                 self._voicesdir],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, startupinfo=si, env=env)
            #: Set when this host says it is ready, out of its own stderr.
            #:
            #: **Alive is not ready, and the difference is the whole of
            #: panthera-speech's interrupt storm.**  A standby is a `Popen`
            #: the instant it exists, so `poll() is None` says yes about a
            #: process that has not yet mapped the engine or opened a channel.
            #: Promoting one of those is not a handoff, it is a cold start
            #: with extra steps.  A `DllHost` sets its own, because `pt_open`
            #: does not return until the engine is up.
            proc.pantheraReady = threading.Event()
        self._watchStderr(proc)
        log.debug("%s: " % self.name + "%shost %d started; abbreviations %s, "
                  "phrasing %r"
                  % ("standby " if standby else "", proc.pid,
                     "on" if self._expandAbbreviations else "OFF",
                     self._phrasing))
        return proc

    @staticmethod
    def _stopHost(proc, graceful=False):
        """Best-effort stop for an active or unused standby process."""
        if proc is None:
            return
        try:
            if graceful:
                proc.stdin.close()
                proc.wait(timeout=1)
            else:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _host(self):
        """Return the resident engine, promoting a standby when possible."""
        with self._procLock:
            if self._restartWanted:
                # Both processes inherited the old environment.  Discard them
                # here, between utterances, then start exactly one with the
                # newly requested phrasing/abbreviation settings.
                old, self._proc = self._proc, None
                spare, self._standby = self._standby, None
                self._restartWanted = False
                self._stopHost(old, graceful=True)
                self._stopHost(spare, graceful=True)
            if self._proc is not None and self._proc.poll() is None:
                return self._proc
            self._proc = None
            if (self._standby is not None
                    and self._standby.poll() is None):
                self._proc, self._standby = self._standby, None
                log.debug("%s: " % self.name + "promoted standby host %d"
                          % self._proc.pid)
                return self._proc
            self._standby = None
            self._proc = self._startHost()
            return self._proc

    def _ensureStandby(self):
        """Start one idle replacement, off NVDA's main thread.

        The host blocks on stdin once initialisation is complete, so sharing
        the auto-reset cancellation event is safe: only the active host has a
        render in flight and waits on that event.

        **There is no spare when the engine is a library.**  One process holds
        one engine and there is no way to unmap it -- a second copy of
        Leopard's Alex would not fit in a 2 GB address space beside the first
        -- so a standby is not something to arrange more cheaply here, it is
        something that cannot exist.  Nothing is lost by it: a spare exists to
        hide the cost of *starting a host*, and starting a session costs the
        1 to 5 ms of opening a channel.  There is nothing left to hide.
        """
        if self._useLibrary():
            return
        with self._procLock:
            if self._stopped or self._restartWanted:
                return
            if self._proc is None or self._proc.poll() is not None:
                return
            if (self._standby is not None
                    and self._standby.poll() is None):
                return
            self._standby = None
            try:
                self._standby = self._startHost(standby=True)
            except Exception as e:
                log.debugWarning("%s: could not start standby host: %s"
                                 % (self.name, e))

    #: The shortest interval between two handoffs, in seconds.
    #:
    #: A handoff is cheap once.  Twenty-six of them in twelve seconds is a
    #: process killed and another spawned on every arrow key, which is what a
    #: user's log showed while he heard two and three second stalls -- and the
    #: spares were all ready, so nothing about readiness explains it.  The
    #: cost is the volume itself.
    #:
    #: So the first interrupt after a quiet moment still gets its instant
    #: promotion, and a burst of them falls back to waiting for the engine's
    #: own cancel, which it answers in a few hundred milliseconds.  Waiting is
    #: the cheaper of the two once the machine is already busy spawning.
    RETIRE_COOLDOWN = 1.0

    #: When the last handoff happened.  A class attribute so no driver
    #: `__init__` has to know this mixin keeps state; the instance shadows it
    #: on the first retirement.
    _lastRetire = 0.0

    def _standbyIsReady(self):
        """-> True when a spare exists, is alive, and has said it is ready.

        `poll()` answers "has this process exited", which a host that is still
        mapping 400 MB of voice has not.  Readiness is the host's own `ready,`
        line, caught by `_watchStderr`.
        """
        with self._procLock:
            spare = self._standby
        if spare is None or spare.poll() is not None:
            return False
        flag = getattr(spare, "pantheraReady", None)
        return bool(flag is not None and flag.is_set())

    #: How long a cancelled response is given to end on its own before the
    #: host is killed instead.
    #:
    #: **Measured, and the number this replaces was measured too -- against a
    #: host that behaved differently.**  When `_abandonHost` was written, a
    #: paragraph of Alex took 815 ms to render and 832 ms rendered then
    #: cancelled after 50 ms: honouring the cancel saved nothing, so killing
    #: the process was the only way to free the worker.
    #:
    #: The host answers a cancel properly now -- the serve loop checks the
    #: event every 10 ms and stops the channel -- and the same measurement
    #: today, cancelling at 50 ms:
    #:
    #:     lion     a long paragraph   588.5 ms  ->   95.8 ms
    #:     leopard  a long paragraph   436.7 ms  ->  133.0 ms
    #:     lion     a single letter    326.8 ms  ->  100.1 ms
    #:
    #: So the wait is already over before a kill could have finished, and
    #: killing costs a fresh start-up and a voice reload -- which for Alex is a
    #: 422 MB bank on Lion and 701 MB on Leopard.
    #:
    #: Reported by Timothy Wynn, who could hear it: "interrupt the voice close
    #: to the start of the utterance, e.g. navigating rapidly by letter.  You
    #: will hear that the executable runs again."  Lion made it constant, for a
    #: reason that is not the driver's fault -- 10.7 never stops its audio
    #: graph, so every utterance sits out a 300 ms quiet window with
    #: `_rendering` still true, and any keystroke inside that window retired
    #: the host.
    #:
    #: **1.5 s, and 500 ms was tried first.**  It is eleven times the worst
    #: number above, which looked absurdly generous and was not: under a
    #: loaded machine -- the full test suite running beside it -- a burst of
    #: eight interruptions still had the worker rendering half a second after
    #: the last cancel, and the backstop fired and retired a host that was
    #: about to answer.  That is the fault this exists to remove, arriving by
    #: a different route on a slower machine.
    #:
    #: The asymmetry says which way to err.  A grace period that is too long
    #: costs a wedged host an extra second before it recovers, and wedges
    #: should now be extinct -- see the deferred audio-graph stop in
    #: `tiger_host_gcd.c`.  One that is too short costs a process restart and
    #: a voice reload on an ordinary keystroke, on exactly the machines least
    #: able to afford it.
    ABANDON_GRACE = 1.5

    #: How long a cancelled render may keep the worker when newer speech is
    #: already waiting behind it.
    #:
    #: This is a different question from the recovery deadline above.  With
    #: nothing waiting, letting an ordinary render finish avoids throwing away
    #: a warm host and reloading a voice bank for no audible benefit.  Once a
    #: replacement utterance is queued, however, every extra millisecond is
    #: silence the user hears after moving to the next item.
    #:
    #: Lion and Snow Leopard logs measured cancelled responses holding that
    #: newer speech for 345 to 947 ms even though its audio rendered in 22 to
    #: 43 ms once the worker became free.  A short handoff grace preserves the
    #: cheap, normal cancellations while bounding that wait.  Host replacement
    #: is already off NVDA's main thread and prewarmed by `_retire()`.
    #:
    #: **Per generation, and `None` means never.**  The trade only pays where
    #: a replacement host is cheaper than the wait.  On Leopard the wait is
    #: short -- the cancel event ends a render in tens of milliseconds -- and
    #: the replacement is a 701 MB voice reload, which is the asymmetry the
    #: recovery deadline above already spells out, and the fault
    #: `test_speech_that_carries_on_after_a_cancel_keeps_its_host` exists to
    #: keep out.  A generation that wants the handoff answers with a number;
    #: the base answers never, so a generation added later cannot inherit a
    #: retirement policy nobody measured on it.
    HANDOFF_GRACE = None

    def _abandonHost(self):
        """Take the host away from an utterance nobody is going to hear --
        but only if it does not let go by itself.

        The worker cannot start the next utterance until it has read the
        current response out of the pipe, and that wait is the lag people
        report around a long post: arrowing past a paragraph, press down and
        hear nothing for the better part of a second; a 6429-character post
        where twelve keypresses over five and a half seconds produced no speech
        at all until the render ended after 7455 ms.  It looks exactly like the
        synthesizer has died, and alt-tabbing appears to fix it only because
        the wait ends on its own.

        Killing the process ends that read in 1.3 ms, measured.  What has
        changed is that it is almost never needed: see `HANDOFF_GRACE` and
        `ABANDON_GRACE`.  Give an ordinary response time to stop cleanly, retire
        it promptly when newer speech is waiting, and reserve the longer
        recovery deadline for a host that is stuck with nothing queued.

        Off this thread, always.  `cancel()` runs on NVDA's main thread, and
        while killing a process is not the pipe -- rule 5 stands -- it does
        take the process lock, and the replacement it starts costs 40 ms.
        Neither belongs in front of a keystroke.
        """
        if self._stopped or self._retiring or not self._rendering:
            return
        self._retiring = True
        try:
            threading.Thread(target=self._retireIfStuck,
                             args=(self._renderSeq,),
                             name=self.name + "-retire", daemon=True).start()
        except Exception:
            self._retiring = False

    def _retireIfStuck(self, seq):
        """Give the response its handoff or recovery grace, then retire it.

        `seq` pins this to the render it was started for, and **the sequence
        number rather than the epoch is what makes it safe.**

        Pinning to the cancel epoch was the first attempt and it was wrong two
        ways at once.  A second cancel inside the grace window bumped the epoch
        and retired this timer -- "a later cancel owns this now", except that
        `_abandonHost` had already refused to start one, seeing `_retiring`
        still set, so nothing owned it and a genuinely stuck host went
        unwatched.  And nothing about the epoch noticed the *good* case: the
        cancelled response ending and ordinary speech carrying on.  For that it
        fell back to polling `_rendering`, which during continuous speech is
        False for microseconds between renders and can be missed on every
        single poll.

        A changed sequence number means the response this was started for is
        over and the worker is free, which is the whole purpose, however the
        flag happened to look when it was read.
        """
        try:
            started = time.time()
            deadline = started + self.ABANDON_GRACE
            while time.time() < deadline:
                if self._stopped or self._renderSeq != seq:
                    return                      # that response is over
                if not self._rendering:
                    return                      # it let go and nothing followed
                replacementWaiting = (self.HANDOFF_GRACE is not None
                                      and not self._queue.empty())
                if replacementWaiting:
                    # Start the process we may need while the old response is
                    # already spending its grace period.  `_ensureStandby`
                    # returns immediately after Popen; the engine continues
                    # initialising beside this timer.  If the response ends
                    # cleanly the spare stays idle for the next handoff.
                    self._ensureStandby()
                    # Popen takes real time, and the cancelled response can
                    # end -- and the queued replacement begin rendering --
                    # while it runs.  Acting on the checks from before the
                    # spawn would retire the host mid-way through speech
                    # somebody wants, so make them again, the same three the
                    # recovery deadline below makes before it acts.
                    if self._stopped or self._renderSeq != seq:
                        return
                    if not self._rendering:
                        return
                    replacementWaiting = not self._queue.empty()
                if (replacementWaiting
                        and time.time() - started >= self.HANDOFF_GRACE
                        and self._standbyIsReady()
                        and (time.time() - self._lastRetire
                             >= self.RETIRE_COOLDOWN)):
                    # The worker cannot take the queued utterance until this
                    # cancelled response ends.  Retire now; the longer deadline
                    # below is only for recovery when no speech is waiting.
                    #
                    # **Only onto a spare that is ready**, and that condition
                    # is what stops a handoff becoming a stampede.  Measured
                    # on this machine, a render lets go of the worker a median
                    # 273-347 ms after a cancel on Lion, 318-366 on Snow
                    # Leopard and 413-791 on Leopard -- against a
                    # `HANDOFF_GRACE` of 60 ms.  So the grace expires long
                    # before the engine could possibly have answered, every
                    # single mid-render interrupt looked stuck, and 26 of 44
                    # utterances in one user's log ended "the host was
                    # retired": each one a process killed, a spare promoted
                    # and another spawned, on a machine already busy.  The
                    # spare promoted at 60 ms was itself 60 ms old.
                    #
                    # Waiting for a ready spare keeps the fast path exactly as
                    # it was -- after any pause there is one, and it is
                    # promoted at once -- while making the cascade impossible,
                    # because during rapid navigation there is never a ready
                    # spare and the engine's own cancel is allowed to land.
                    # `ABANDON_GRACE` below is still the backstop for a host
                    # that really has stopped answering.
                    if log.isEnabledFor(log.DEBUG):
                        log.debug(
                            "%s: render %d still holds the worker %.0f ms "
                            "after a cancel while newer speech waits; "
                            "retiring the host"
                            % (self.name, seq,
                               (time.time() - started) * 1000.0))
                    self._retire()
                    return
                time.sleep(0.01)
            if self._stopped or self._renderSeq != seq or not self._rendering:
                return
            log.debugWarning(
                "%s: render %d has not answered %.1f s after a cancel; "
                "retiring the host"
                % (self.name, seq, self.ABANDON_GRACE))
            self._retire()
        finally:
            self._retiring = False

    def _retire(self):
        """Replace the host, using an already-started standby when available.

        The swap happens before the old process is killed.  Its blocked reader
        then wakes, sees that its process is no longer current, and retries the
        still-wanted text on the replacement without waiting for another
        engine process to be created.
        """
        try:
            replacement = None
            discard = None
            with self._procLock:
                proc, self._proc = self._proc, None
                if (not self._stopped and not self._restartWanted
                        and self._standby is not None
                        and self._standby.poll() is None):
                    replacement, self._standby = self._standby, None
                    self._proc = replacement
                elif self._standby is not None:
                    discard, self._standby = self._standby, None
            self._stopHost(proc)
            self._stopHost(discard)
            self._lastRetire = time.time()
            if not self._stopped:
                if replacement is not None:
                    log.debug("%s: " % self.name + "promoted standby host %d"
                              % replacement.pid)
                    # Replenish the reserve now.  This retirement thread is
                    # already off NVDA's main thread, and by the next rapid
                    # navigation key the new spare will normally be ready.
                    self._ensureStandby()
                else:
                    try:
                        self._host()
                    except Exception:
                        pass
        finally:
            self._retiring = False

    def _makeCancelEvent(self):
        """A Windows event the host can watch, named so the child can open it.

        Best effort throughout: every failure here costs responsiveness after
        an interruption and nothing else, so none of it is worth raising over.
        """
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            name = ("Local\\%s-cancel-%d-%d"
                    % (self.name, os.getpid(), id(self)))
            # Manual reset off, initial state off: the host consumes the signal
            # by waiting on it, and the worker clears any stale one before it
            # sends the next request.
            h = k32.CreateEventW(None, False, False, name)
            if h:
                self._cancelEvent = h
                self._cancelEventName = name
        except Exception as e:
            log.debugWarning("%s: " % self.name + "no cancel event (%s)" % e)

    def _signalCancel(self):
        """Tell the host to give up on what it is rendering.  Never blocks."""
        if not self._cancelEvent:
            return
        try:
            import ctypes
            ctypes.windll.kernel32.SetEvent(self._cancelEvent)
        except Exception:
            pass

    def _clearCancel(self):
        """Drop a stale signal, so a cancel cannot kill the utterance after."""
        if not self._cancelEvent:
            return
        try:
            import ctypes
            ctypes.windll.kernel32.ResetEvent(self._cancelEvent)
        except Exception:
            pass

    def _watchStderr(self, proc):
        """Put whatever the host complains about into NVDA's log.

        It costs a thread and earns the only diagnosis anyone will ever get
        from a machine we do not have.  Vicki's AAC decoding is done by
        whatever decoder that copy of Windows ships, and one that behaves
        differently makes her sound wrong rather than silent -- the host says
        so on stderr, and with this that lands in a log the user can send.

        The thread also has to exist for its own sake: a pipe nobody reads
        fills up, and then the host blocks inside a printf and the screen
        reader goes quiet.  DEVNULL avoided that by throwing the evidence away.
        """
        if proc.stderr is None:
            # A later library session: the log belongs to the process and the
            # first session took it, so there is nothing here to read.  One
            # reader per pipe, or two would take turns and split lines.
            return

        def pump():
            try:
                for line in iter(proc.stderr.readline, b""):
                    line = line.decode("utf-8", "replace").rstrip()
                    if not line:
                        continue
                    # The host prefixes anything it actually wants a person to
                    # read with its own name.  Everything else is commentary
                    # and belongs at debug level, or a user's log fills with
                    # several hundred lines of loader detail.
                    if line.startswith("tiger_host:"):
                        #: The host's own word for it, and the only honest
                        #: signal available: everything before this line is
                        #: mapping, binding and running initialisers.
                        if line[11:].lstrip().startswith("ready,"):
                            try:
                                proc.pantheraReady.set()
                            except Exception:
                                pass
                    if not line.startswith("tiger_host:"):
                        log.debug("%s host: " % self.name + "%s" % line)
                    elif line[11:].lstrip().startswith(_HOST_ROUTINE):
                        # Said once per host, and the host is started again
                        # after every interruption, so at warning level this
                        # is three or four lines per arrow key.  One real log
                        # was nothing else: forty startups in ninety seconds,
                        # burying the one line that mattered.  It is still
                        # here at debug, where the rest of the startup is.
                        log.debug("%s host: " % self.name + "%s" % line)
                    else:
                        log.warning("%s host: " % self.name + "%s" % line)
            except Exception:
                pass
            finally:
                try:
                    proc.stderr.close()
                except Exception:
                    pass
        t = threading.Thread(target=pump, name=self.name + "-host-log")
        t.daemon = True
        t.start()

    def _render(self, text, wpm, voice, pitch=0, sink=None, volume=0):
        """-> PCM bytes, or None.  One request, one utterance.

        With a `sink`, the audio is asked for in chunks and each is handed over
        as it arrives, and the return is `b""` because the audio has already
        gone.  A sink returning False stops the feeding without abandoning the
        response.  Alex is the reason this matters most: he renders more audio
        per character than any other voice here, so a paragraph of him was the
        longest wait of all.
        """
        text = text.strip()
        if not text:
            return b""
        if not self._acceptCommands:
            # The front end really does parse "[[rate 100]]", "[[volm 0.5]]"
            # and even "[[inpt TUNE]]" -- all measured working.  That is a
            # lovely feature and a hazard: a web page or a file name containing
            # "[[" could otherwise change how the screen reader sounds.
            #
            # Removing the sequence is what works.  Putting a space between the
            # brackets does not -- the parser tolerates "[ [" -- and turning the
            # delimiters off with soCommandDelimiter made text containing "[["
            # produce silence instead of speaking it.  The host separately
            # guarantees no command can outlive its utterance.
            text = COMMAND_RE.sub("", text)
        elif self.INPUT_MODES_WORK is False:
            #: **The user asked for embedded commands and gets all of them
            #: except the ones this generation cannot honour.**
            #:
            #: 10.7 ignores the `{D …; P …}` annotations that make `inpt TUNE`
            #: worth having, and a malformed phoneme after `inpt PHON` faults
            #: inside Apple's own `SLLexerImpl::Error` and takes the host with
            #: it.  See panthera-speech#6.
            #:
            #: Removing just this family rather than the whole checkbox is the
            #: point: `[[slnc]]`, `[[rate]]`, `[[volm]]` and `[[char]]` are all
            #: measured working on 10.7, and somebody who turned commands on
            #: probably wanted those.  What is left when the mode switch is
            #: stripped is the phoneme or note source read as ordinary text --
            #: which sounds wrong, and is *meant* to: it says the mode did not
            #: engage, where silence would have said nothing at all.
            text = INPUT_MODE_RE.sub("", text)
        if self._acceptCommands:
            #: **An unclosed input mode survives the utterance boundary.**
            #: Say-all hands a tune file over in pieces and only the first
            #: piece carries its `[[inpt TUNE]]`; the engine starts every
            #: utterance in text mode, so verse two used to arrive as two
            #: minutes of spoken annotations (panthera-speech#9 -- measured,
            #: 1.97 s of song and then 110 s per verse).  So the driver
            #: carries it: an utterance that switched without closing leaves
            #: the next one beginning with the same switch, until an
            #: `[[inpt TEXT]]` closes it or a cancel says the person has
            #: moved on to something that is not the tune file.
            #:
            #: The scan runs on the text *after* the prepend, which is what
            #: makes "no switch in this piece" leave the carried mode in
            #: force rather than dropping it.
            if self._inputMode:
                text = "[[inpt %s]] " % self._inputMode + text
            lastSwitch = None
            for m in INPUT_MODE_CAPTURE_RE.finditer(text):
                lastSwitch = m.group(1).upper()
            if lastSwitch:
                self._inputMode = (None if lastSwitch == "TEXT"
                                   else lastSwitch)
        if self._numberStyle != "off":
            #: Only between the commands, never inside one.  With embedded
            #: commands accepted, "[[rate 200]]" is still in the text at this
            #: point, and rewriting the 200 inside it would leave the engine
            #: reading "[[rate two hundred]]" as an unparseable command.
            text = "".join(
                part if part.startswith("[[")
                else pantheranumbers.expand(part, self._numberStyle)
                for part in COMMAND_SPLIT_RE.split(text))
        #: The engine's measured wrong guesses, settled in the text whichever
        #: way the abbreviations setting points: "<proper noun> Dr." read as
        #: a street mid-news-article, and "X's" after NVDA's camel-case split
        #: read as the roman numeral -- "SpaceX's" was "space ten's".  See
        #: pantheraabbrev.disambiguate and [[news-reading-quirks]].
        text = "".join(
            part if part.startswith("[[")
            else pantheraabbrev.disambiguate(part, self._expandAbbreviations)
            for part in COMMAND_SPLIT_RE.split(text))
        if not self._expandAbbreviations:
            #: **The engine's own abbreviation table, which no setting of its
            #: own reaches.**  TIGER_NO_ABBREV turns off the dictionary rules
            #: that rewrite units and quantities, and those are regular
            #: expressions this host compiles, so declining to compile one
            #: turns it off.  "DR" is not one of them: it is a lexical entry
            #: inside MacinTalk, tagged `Abbrev` and `Doctor`, and rendering it
            #: runs no regular expression and no query at all -- and neither
            #: is "Dr.", "St." or 10.7's digit-adjacent units, which is why
            #: the despelling now covers those forms too.
            #:
            #: So the switch only did half of what its label promised, and the
            #: half it missed is the half Tomi noticed.  See pantheraabbrev.py
            #: for what is covered and what deliberately is not.
            text = "".join(
                part if part.startswith("[[") else pantheraabbrev.spell(part)
                for part in COMMAND_SPLIT_RE.split(text))
        if self._fixStress:
            #: Outside the commands for the same reason the numbers are: a
            #: respelling inside "[[rate 200]]" would corrupt the command.
            #:
            #: The word arrives already spelt out -- NVDA's symbol dictionary
            #: turns ":" into "colon" long before the driver is handed the
            #: text, which is why the engine never sees the punctuation at all.
            #: Running after the number expansion simply keeps the two rewrites
            #: from meeting: that one works on digits, this one on words.
            text = "".join(
                part if part.startswith("[[") else pantherastress.fix(part)
                for part in COMMAND_SPLIT_RE.split(text))
        # Volume is the engine's own [[volm]] command, not gain applied to
        # the PCM afterwards.  Measured on both engines it is exactly
        # linear -- volm 0.5 halves the RMS and 0.2 fifths it -- so the
        # synthesizer does the arithmetic in floating point before it
        # quantises, which is better than anything done to 16-bit samples
        # after the fact.  Nothing is added at full volume, so the default
        # request is byte-for-byte what it always was.
        # Inflection is the engine's own 'pmod', as an embedded command --
        # the same trick as volume, and for the same reason: no protocol
        # change, and the engine does the work.  Nothing is sent at the
        # halfway point so the default utterance is byte-for-byte what it
        # has always been.
        # Coming back to the default has to be *said*.
        #
        # These commands set state on the speech channel and it outlives the
        # utterance that set it.  Sending nothing at the default therefore
        # does not mean "the default", it means "whatever was set last" -- so
        # dropping the volume to zero and putting it back to 100 left the
        # synthesizer silent for good, and only 99 brought it back.  A user
        # found that; it is the worst failure this driver has.
        #
        # So the command is sent when the setting is not the default, and an
        # utterance from a driver whose settings were never touched still
        # carries no embedded commands at all.
        #
        # **But coming back cannot be said, because there is no way to say
        # it.**  "[[pmod 100]]" reads as the obvious way to mean "back to
        # normal", and for eleven of Leopard's twenty-four voices it is --
        # and for the other thirteen it is simply a different number from
        # the one that voice was recorded with.  Measured: Albert, Alex,
        # Bahh, Boing, Cellos, Deranged, Junior, Kathy, Organ, Princess,
        # Trinoids, Vicki and Zarvox are all *changed* by it and stay
        # changed.  Alex is the worst of them, because he ignores a raised
        # pmod outright -- so the command sent to undo an inflection he
        # never had was the only thing that ever altered his voice.
        #
        # pmod is a percentage of a depth that belongs to the voice, and
        # this driver has no table of those depths and should not grow one.
        # A channel that has just been opened is at whatever the voice's own
        # depth is, whichever voice it is.  So the way back to the default
        # is a new engine, and `_restartHost` is already the safe way to ask
        # for one -- it raises a flag that `_host()` acts on a few lines
        # below, on this thread, before this utterance is sent.  It costs
        # one start-up, on the single utterance where the slider comes home.
        if self._inflection != 50:
            pmod = int(self._inflection * INFLECTION_MAX_PMOD / 100)
            text = "[[pmod %d]]%s" % (pmod, text)
            self._inflectionSent = True
        elif self._inflectionSent:
            self._restartHost()
            self._inflectionSent = False
        # **Volume is always sent now, and that is the simpler rule.**
        #
        # It used to be sent only when it was not the default, with one more
        # on the way back, precisely because the command sets channel state
        # that outlives the utterance -- the "silent for good at 0, and only
        # 99 brings it back" failure above. That bookkeeping is gone: the
        # level is per-voice, so it has to be restated whenever the voice
        # changes anyway, and restating it every time is both cheaper to
        # reason about and impossible to get wrong.
        #
        # `volm 1.000` renders byte-identically to sending nothing, measured,
        # so the voices whose factor is 1.0 -- Bruce, Victoria, Agnes -- are
        # exactly as they were.
        #: `volume` is a VolumeCommand offset on NVDA's 0-100 scale, 0
        #: meaning the user's own setting -- clamped, like the rate and pitch
        #: offsets beside it.
        level = min(100, max(0, self._volume + volume))
        text = "[[volm %.3f]]%s" % (
            volume_volm(level, voice, self.VOLUME_NORM), text)
        #: Ours, and only ours.  A cancel can retire this process and
        #: start its replacement while this call is still in the read
        #: below, and the failure that follows must not kill the host the
        #: next utterance is about to use.
        proc = None
        #: Whether the host answered at all.  It is what separates a host
        #: that cannot stream from one that was taken away mid-stream, and
        #: only the first of those is a reason to stop asking.
        answered = False
        try:
            proc = self._host()
            log.debug("%s: " % self.name + "utterance -> host %d: %r"
                      % (proc.pid, text[:60]))
            v = voice.encode("utf-8")
            t = _encode(text)
            # A cancel that arrived while nothing was rendering must not be
            # waiting here to kill the utterance that follows it.
            self._clearCancel()
            streaming = sink is not None and self._streaming
            req = REQ_MAGIC_STREAM if streaming else REQ_MAGIC
            # From here until the response ends, this is what cancel() may
            # take the host away from.
            self._rendering = True
            self._renderSeq += 1
            proc.stdin.write(struct.pack("<IiiIII", req, wpm, pitch,
                                         0, len(v), len(t)) + v + t)
            proc.stdin.flush()
            if not streaming:
                magic, status, nframes = struct.unpack(
                    "<IiI", _readExactly(proc.stdout, 12))
                if magic != RSP_MAGIC:
                    raise IOError("bad response magic %08x" % magic)
                answered = True
                pcm = _readExactly(proc.stdout, nframes * 2)
                if status:
                    log.debugWarning("%s: " % self.name + "OSErr %d for %r"
                                     % (status, text))
                if sink is not None:
                    # Streaming is off, but the caller still expects its audio
                    # through the sink.  One chunk: the whole utterance, which
                    # is what this driver did before it streamed.
                    if pcm:
                        sink(pcm)
                    return b""
                return pcm
            # Streamed.  The status arrives first, because SESpeakBuffer
            # returns in a tenth of a millisecond and the outcome is known long
            # before the audio is.
            magic, status = struct.unpack("<Ii", _readExactly(proc.stdout, 8))
            if magic != RSP_MAGIC:
                raise IOError("bad response magic %08x" % magic)
            answered = True
            feeding = True
            while True:
                (n,) = struct.unpack("<I", _readExactly(proc.stdout, 4))
                if not n:
                    break
                chunk = _readExactly(proc.stdout, n * 2)
                # Read to the end of the response even once the sink has
                # stopped wanting it.  The same pipe carries the next
                # utterance, so chunks left unread would put the protocol out
                # of step -- and this driver's answer to that is to kill the
                # engine, which costs far more than reading audio nobody will
                # hear.  Alex renders at about ninety times real time, so
                # draining is cheap.
                if feeding and not sink(chunk):
                    feeding = False
            if status:
                log.debugWarning("%s: " % self.name + "OSErr %d for %r"
                                 % (status, text))
            return b""
        except Exception as e:
            # The protocol is a stream: a failed exchange leaves it out of step,
            # so drop the process rather than trying to resynchronise.
            log.debugWarning("%s: " % self.name + "%s" % e)
            #: Whether somebody else took this process away, rather than it
            #: dying on us.  `_retire`, `_host` and `terminate` all swap
            #: `self._proc` under this lock *before* they kill or close, so a
            #: process that is no longer the current one was retired.
            retired = False
            with self._procLock:
                # Only the process this call was using.  A cancel may
                # already have retired it and started its replacement, and
                # killing *that* would throw away the host the next
                # utterance needs -- for ever, one utterance at a time.
                if proc is not None and self._proc is proc:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                    self._proc = None
                elif proc is not None:
                    retired = True
            if (sink is not None and self._streaming and not answered
                    and not retired):
                # A host that does not know 'TGR4' exits rather than answer it,
                # which arrives here as a closed pipe.  Left alone this repeats
                # for every utterance -- respawn, refuse, respawn -- and the
                # user hears nothing at all, ever.  This actually happened
                # here, with an executable one build out of date.  So stop
                # asking, and say so where somebody will see it.
                #
                # **But a cancel closes the pipe in exactly the same way.**
                # `_abandonHost` kills the host mid-request precisely so the
                # worker is not left reading a response nobody wants, and
                # `answered` cannot tell that apart from a refusal: both are
                # a request written and no magic read back.  So a burst of
                # interruptions turned streaming off for the session and told
                # the user to reinstall a perfectly good add-on -- seen here
                # in a real log, after which every utterance went back to
                # arriving in one piece.
                #
                # `retired` is what separates them.  A host that refuses is
                # still the current one when its pipe closes; a host taken
                # away was swapped out under `_procLock` first.  Note that
                # "has it ever streamed?" does *not* work here: an add-on
                # updated under a running NVDA really can start refusing
                # after a session of success, which is what
                # test_an_engine_that_cannot_stream_still_speaks asserts.
                self._streaming = False
                log.warning("%s: " % self.name + "the bundled engine does not "
                            "understand streamed audio, which means its files "
                            "are older than this driver -- reinstall the "
                            "add-on. Speaking the previous way instead.")
            return None
        finally:
            self._rendering = False

    def _restartHost(self):
        """Ask for a fresh engine process before the next utterance.

        Both engine settings are read from the environment when the host
        starts, so a change cannot reach a process already running.

        **Ask, rather than kill.**  The first version closed the host's stdin
        here, on NVDA's thread, while the worker could be halfway through a
        streamed utterance -- and a pipe that closes mid-stream is exactly how
        an engine too old to stream announces itself, so the driver switched
        streaming off for the rest of the session and told the user to
        reinstall.  Toggling a checkbox twice was enough to do it.

        So the swap happens where it is safe: `_host()` runs at the start of
        each render, on the worker thread, with nothing in flight."""
        self._restartWanted = True
