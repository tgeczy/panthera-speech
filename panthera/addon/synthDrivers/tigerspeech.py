# -*- coding: utf-8 -*-
"""NVDA speaking with Mac OS X 10.4 Tiger's MacinTalk, as native code.

Not a bridge and not an emulator.  `tiger_host.exe` is a 32-bit process that
maps Apple's i386 MacinTalk and SpeechDictionary into itself, fills the pointer
slots dyld would have filled, and calls `SESpeakBuffer` directly.

**The host is 32-bit because the engine is i386, and there is no second build
to make**: a 64-bit process cannot load i386 code at all.  Keeping it in its
own process is exactly what makes this add-on indifferent to NVDA's own
bitness -- the same binary serves 32-bit NVDA 2023.1 and 64-bit NVDA 2026.1.
That is the opposite trade-off from the sibling ROM add-on, which loads its
emulator in-process and therefore has to ship one DLL per architecture.

Nothing of Apple's ships here.  The user supplies their own Tiger install; the
engine and voices are read from wherever they extracted it, and `package.py`
refuses to build an add-on containing either.

An utterance costs about 12 ms, so there is no cache and no prewarming -- the
things the VM bridge needed to be usable at all.  What replaced them is a
single measured trick, documented in the host: the engine schedules against the
wall clock, so its clock runs 128x fast and the audio comes out byte-identical.

The rules below are not this driver's; they were paid for in the sibling ROM
add-on, each by breaking it and being told.  They apply here unchanged.

1. *Never block the render loop on playback.*  `WavePlayer.feed()` blocks until
   the device has room, for as long as the audio lasts, and NVDA paces what it
   sends on `synthDoneSpeaking`.  Blocking there costs a letter of latency per
   keystroke.  Feeding gets its own thread.

2. *Never let rendered audio wait in a holding area to be discarded.*  This
   used to read "hand the player a whole utterance at a time", because slicing
   one into chunks was how the holding area appeared -- measured, 367 of 435
   utterances thrown away in one session, heard as words cut in half.

   The audio is now streamed, so it genuinely does arrive in chunks; what
   makes that safe is that no chunk ever waits.  Each one goes straight to the
   audio queue as it comes off the pipe, and the only thing that stops it is
   the user having cancelled, which is the one case where cutting a word in
   half is the correct answer.  The rule that was really being kept is the one
   stated above; the whole-utterance version was the shape it happened to take
   when the audio arrived all at once.  Waiting for it all cost most of a
   second before the first sound of a paragraph, and none of that was the
   engine -- it renders at about ninety times real time.

3. *Discard work by draining, never by stamping it.*  A generation counter
   compared at render time froze that driver silent -- 615 utterances spoken,
   then 194 consecutive items discarded unheard, with no recovery.
   **Permanently silent is a far worse failure than occasionally speaking
   something stale.**  The VM bridge did stamp, because a render took 1.4 s and
   the window was wide; at 12 ms it is not worth the risk it carries.

4. *`cancel()` must ALWAYS stop the player*, ungated.  Restarting an output
   stream is a performance question; interrupting is a correctness one.

5. *Only the worker talks to the engine.*  `cancel()` runs on NVDA's main
   thread -- the one that turns keystrokes into speech -- so it never touches
   the pipe.  Settings record what the user asked for and the worker
   reconciles before each utterance, rather than queueing events: `cancel()`
   drains that queue, and NVDA cancels between changing a setting and speaking
   the confirmation of it.
"""
import codecs
import os
import re
import struct
import subprocess
import threading
import time
import queue

import nvwave
import speech.commands
from logHandler import log
from autoSettingsUtils.driverSetting import BooleanDriverSetting, DriverSetting
from autoSettingsUtils.utils import StringParameterInfo
from synthDriverHandler import (SynthDriver, VoiceInfo, synthDoneSpeaking,
                                synthIndexReached)

#: Sample format the engine renders in.  Read from the StreamFormat it sets,
#: not assumed: 22050 Hz, mono, and the host converts its 32-bit float to 16.
OUT_RATE = 22050

#: How far ahead of the speakers the feeder may run, in seconds.
#:
#: Small enough that an interrupt cannot leave much behind, large
#: enough that playback never catches up with the renderer.
FEED_LEAD = 0.35

#: NVDA's 0-100 rate onto words per minute.  180 is Tiger's own default and
#: lands mid-slider, so the control behaves the way people expect.
RATE_MIN, RATE_MAX = 80, 400

#: The top of the slider with rate boost on.
#:
#: 400 was never the engine's limit, it was ours.  Measured, the engine
#: honours whatever it is asked for and stays stable well past anything
#: useful: Alex delivers 853 wpm when asked for 800, and 1598 when asked
#: for 1500, without a stumble.  A user asked how to get past 100% and the
#: honest answer was that nothing was stopping us but a constant.
#:
#: It is a separate switch rather than a wider slider because widening the
#: slider would silently make everyone's existing setting faster -- the
#: same mistake as a volume control that defaults to half.
RATE_MAX_BOOST = 1200

#: NVDA's 0-100 pitch onto an offset from the voice's own pitch, in tenths of
#: a semitone.  50 is the voice as Apple recorded it; the ends are an octave
#: either way, which is as far as any of these stay recognisable.
#:
#: An offset rather than an absolute value because every voice has its own
#: natural pitch -- Fred sits near 127 Hz, Bruce near 135 -- so an absolute
#: scale would make the middle of the slider mean something different for each.
#: The host asks the engine for the voice's own 'pbas' and adds this to it.
PITCH_SEMITONES = 12

#: NVDA's 0-100 inflection onto the engine's 'pmod', which is a percentage:
#: 0 is a monotone, 100 is roughly the voice as recorded, 200 is twice its
#: usual movement.  Measured on Alex over one sentence, mean F0 and how far
#: it wanders:
#:
#:     pmod   0    100.0 Hz, spread  8.6   (flat)
#:     pmod 100    111.3 Hz, spread 13.7
#:     pmod 200    121.5 Hz, spread 22.5   (very expressive)
#:
#: Nothing is sent at the halfway point, because no command at all is not
#: quite the same as pmod 100 -- untouched measures 117.0 Hz and 16.8 --
#: and the default has to be the engine exactly as it comes.
INFLECTION_MAX_PMOD = 200

#: An embedded speech command, as Tiger's front end parses it.  Non-greedy, and
#: it will not run past a newline, so an unclosed "[[" cannot eat a paragraph.
COMMAND_RE = re.compile(r"\[\[[^\]]{0,64}\]\]")

#: Characters MacRoman has no room for, mapped to something it can say.
#: Everything typographic that matters -- em dash, en dash, curly quotes,
#: ellipsis -- MacRoman already has, so it is not listed here.
_FOLD = {
    0x00A0: u" ", 0x2007: u" ", 0x2009: u" ", 0x202F: u" ",   # fixed spaces
    0x2011: u"-", 0x2012: u"-", 0x2015: u"-", 0x2212: u"-",   # more dashes
    0x2032: u"'", 0x2033: u'"', 0x02BC: u"'",                 # primes
    #: The typographic apostrophe, and the reason a sentence full of them
    #: fell apart.  MacRoman *has* it, at 0xD5 -- but 0xD5 is the right
    #: single QUOTATION mark, and the engine's front end treats it as one:
    #: it breaks the phrase there.  "Canopy’s investments" came out as
    #: "Canopy" - 250 ms of silence - "s investments", and the sentence ran
    #: 1.57 s longer for the pauses it grew.  A straight apostrophe is an
    #: apostrophe, so these are folded before encoding.  Curly *double*
    #: quotes are left alone: those really are quotation marks.
    0x2018: u"'", 0x2019: u"'",
    0x2044: u"/",                                             # fraction slash
}


def _unmappable(err):
    """Anything MacRoman cannot spell becomes a space.

    The alternative, `errors="replace"`, produces "?", and the engine reads a
    question mark as a question -- it lifts the intonation of the whole
    sentence.  A gap is closer to the truth than a wrong inflection, and it
    leaves a real "?" typed by the user meaning what it says.
    """
    return (u" " * (err.end - err.start), err.end)


codecs.register_error("tigerspeech_fold", _unmappable)


def _encode(text):
    """-> the engine's bytes.

    **The engine's text is a single-byte Mac encoding, not UTF-8.**  Sent as
    UTF-8, one em dash arrived as three bytes and was read a character at a
    time: "he paused - then left" came out as "he paused, he eyed and left",
    and smart quotes as "ah".  MacRoman puts the em dash at 0xD1, the curly
    quotes at 0xD2 to 0xD5 and the ellipsis at 0xC9, so encoding properly is
    the whole fix -- there is no table of symbol names to maintain.
    """
    return text.translate(_FOLD).encode("mac_roman", "tigerspeech_fold")


def _fullVolumeByDefault(setting):
    """NVDA defaults a numeric driver setting to 50, and volume is one.

    `NumericDriverSetting` takes `defaultVal=50`, and NVDA writes that over
    whatever the driver put in `__init__` -- autoSettings.py does
    `setattr(inst, setting.id, setting.defaultVal)`.  So adding a volume
    control made everybody quieter the moment they upgraded, which is exactly
    what a tester reported: "alex got quieter, not by a whole lot, but it was
    definitely noticeable".

    Full is the right default for a synthesizer that had no volume control at
    all yesterday: upgrading should change nothing until the user asks it to.
    """
    setting.defaultVal = 100
    return setting


def _silence(ms):
    """-> that many milliseconds of 16-bit mono silence."""
    if ms <= 0:
        return b""
    return b"\0" * (2 * int(OUT_RATE * ms / 1000.0))


def _joinFragments(parts):
    """Join the pieces of one utterance back into a sentence.

    A space goes in only where neither side already has one: NVDA's fragments
    usually carry their own spacing, and doubling it is harmless, but "link"
    followed by "Home" with nothing between them would otherwise be handed to
    the engine as "linkHome" and spoken as one word.
    """
    out = []
    for part in parts:
        if out and part[:1].strip() and out[-1][-1:].strip():
            out.append(" ")
        out.append(part)
    return "".join(out)


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

# Finding the engine lives in `tree`, not here: the global plugin that offers
# to open the folder needs exactly the same answer, and two copies of a lookup
# is two chances to disagree about where the engine is.
# Imported out of the package, under an alias, because one `sys.modules` is
# shared by every add-on NVDA loads. The Tiger and Leopard add-ons both
# used to call this module `tree`, so whichever loaded first won and the
# other silently ran its sibling's lookup -- reading the wrong folder and
# offering the wrong voices, with nothing failing. A package settles that for
# good: nothing here is reached through `sys.path` any more.
from ._panthera import dllhost                                # noqa: E402
from ._panthera import pantheratiger as tree                  # noqa: E402

HOST_EXE = tree.HOST_EXE
HOST_DLL = tree.HOST_DLL
find_tree = tree.find_tree
engine_paths = tree.engine_paths
read_voices = tree.read_voices
config_base = tree.config_base


_MISSING = (
    "Tiger speech cannot start, because the engine is not there yet.\n\n"
    "This add-on ships no part of Apple's software. You supply it from your "
    "own Mac OS X 10.4 install disc, and put the extracted Speech folder and "
    "SpeechDictionary.framework into:\n\n"
    "%s\n\n"
    "The extract_tiger.py tool in the project repository will do that for you "
    "from an installer image, and there is a README in that folder with the "
    "details. NVDA's log has the full list of what was found and what was "
    "missing.\n\n"
    "Open that folder now?"
)


def _explainLater(folder, reason=None):
    """Show the engine-missing dialog once NVDA has finished failing.

    With a `reason`, that is shown instead: a tree which is present but cannot
    run must not be told its engine is missing. Someone who has already
    extracted an engine and is then sent back to the folder to put one there
    has been given a wrong instruction and no way to know it.

    Never straight from `__init__`: a modal dialog there would stall the
    synthesizer switch with speech half torn down. Queued instead, so it
    arrives after NVDA has fallen back to the previous synthesizer and speech
    is working again -- which it always does, so the user is never stranded.

    It lands on top of NVDA's own "Could not load the tigerspeech synthesizer"
    box rather than after it, because that box runs a nested event loop which
    dispatches this. That ordering is the right way round: ours is the one with
    something to act on.
    """
    try:
        import wx
        import gui
    except ImportError:
        return

    def show():
        try:
            if reason:
                gui.messageBox(reason, "Tiger speech",
                               wx.OK | wx.ICON_INFORMATION)
                return
            answer = gui.messageBox(_MISSING % folder, "Tiger speech",
                                    wx.YES_NO | wx.ICON_INFORMATION)
            if answer == wx.YES:
                os.makedirs(folder, exist_ok=True)
                os.startfile(folder)
        except Exception:
            log.error("tiger-speech: could not show the engine dialog",
                      exc_info=True)
    wx.CallAfter(show)


class SynthDriver(SynthDriver):
    name = "tigerspeech"
    description = _("Tiger speech (MacinTalk 3.3)")

    #: `[[inpt PHON]]` and `[[inpt TUNE]]` do what they say on 3.3 -- the
    #: singing the manifest advertises *is* this, and it is measured working.
    #: Only 10.7 has to answer no; see `lionspeech` and panthera-speech#6.
    #:
    #: Declared even though this driver has its own body and inherits no
    #: default, because the test asks every generation the question -- and an
    #: attribute that happens to exist on three drivers out of four is how the
    #: fourth silently opts out of being asked.
    INPUT_MODES_WORK = True

    supportedSettings = (
        SynthDriver.VoiceSetting(),
        SynthDriver.RateSetting(),
        SynthDriver.PitchSetting(),
        _fullVolumeByDefault(SynthDriver.VolumeSetting()),
        SynthDriver.InflectionSetting(),
        BooleanDriverSetting(
            "acceptCommands",
            _("Accept &embedded speech commands in text"),
            defaultVal=False,
        ),
        BooleanDriverSetting(
            "rateBoost",
            _("&Rate boost"),
            defaultVal=False,
            availableInSettingsRing=True,
        ),
        DriverSetting(
            "pauseMode",
            _("&Pause between phrases"),
            defaultVal="short",
        ),
    )
    #: **Advisory, not a filter.**  NVDA does not strip a command a driver
    #: leaves out of this set -- it arrives at `speak()` anyway and is dropped
    #: there in silence, while callers that *do* consult the set (MathCAT is
    #: one) decline to send it at all.  Two quiet failures from one omission.
    #:
    #: `RateCommand` and `VolumeCommand` were missing until 0.98.1, reported by
    #: Amir against the Leopard and Lion generations; this one had the same
    #: gap.  NVDA never emits either itself -- only SSML and add-ons do -- so
    #: no amount of ordinary use would have found it.
    supportedCommands = {speech.commands.IndexCommand,
                         speech.commands.BreakCommand,
                         speech.commands.PitchCommand,
                         speech.commands.RateCommand,
                         speech.commands.VolumeCommand}
    supportedNotifications = {synthIndexReached, synthDoneSpeaking}

    @classmethod
    def check(cls):
        """**Listed only when there is an engine to run.**

        Both halves of this were once right and they disagreed.  Tiger and
        Leopard were always offered and explained themselves in a dialog when
        chosen, because hiding them had left people with an add-on, no
        synthesizer and nothing to go on.  Lion, added later, listed itself
        only when it had an engine, because nobody should arrow past
        synthesizers that cannot speak to reach one that can.

        Timothy Wynn found the combination: install with no data at all and
        `Leopard speech (Alex, MacinTalk 3.6)` is sitting there, selectable and
        mute, while Lion -- equally dataless -- is not.

        `synthDrivers/pantheraspeech.py` is what makes hiding safe here.  When
        no generation can speak it takes their place, as one entry, and opens
        the tool that fixes it.  So there is still a route to the explanation,
        which is the thing whose absence made hiding wrong the first time.
        """
        return tree.usable()

    def __init__(self):
        super().__init__()
        ok, lines = tree.explain()
        if not ok:
            log.warning("tiger-speech cannot start:\n  %s" % "\n  ".join(lines))
            found = find_tree()
            _explainLater(tree.config_dir(),
                          tree.unsupported_build(found) if found else None)
            raise RuntimeError("tiger-speech has no engine to run")
        self._tree = find_tree()
        if not self._tree:
            raise RuntimeError("no Tiger speech tree found")
        self._mt, self._sd, self._voicesdir = engine_paths(self._tree)
        self._voices = read_voices(self._voicesdir, playable_only=True)
        if not self._voices:
            raise RuntimeError("no voices in %s" % self._voicesdir)

        self._rate = 50
        self._pitch = 50
        self._acceptCommands = False
        self._pauseMode = "short"
        self._rateBoost = False
        self._inflection = 50
        self._volume = 100
        #: Whether a non-default volume or inflection has been sent to the
        #: engine and is still in force on the channel.
        self._volumeSent = False
        self._inflectionSent = False
        self._voiceId = self._voices[0][0]
        for bundle, _display, engine in self._voices:      # prefer Fred
            if bundle == "Fred":
                self._voiceId = bundle
                break

        self._proc = None
        #: Set when the channel has to be replaced rather than talked back to
        #: its default.  See the inflection block in `_render`.
        self._restartWanted = False
        self._procLock = threading.Lock()
        self._stopped = False
        #: Bumped by `cancel()`.  Read once at the start of a streamed render
        #: and compared while it runs, so that audio for an utterance the user
        #: has already interrupted stops being fed.
        #:
        #: This is not the generation stamp rule 3 forbids.  That one decided
        #: whether an utterance was rendered or emitted *at all*, and a lost
        #: race left the driver permanently silent.  This one can only ever
        #: shorten the tail of a stream that is already playing; the next
        #: utterance re-reads it, so no sequence of races can stop the driver
        #: speaking.
        self._cancels = 0
        #: Whether the bundled host understands a streamed request.
        #:
        #: It ships with this file, so it always should.  But an add-on update
        #: whose executable failed to copy -- the reload trap -- would leave a
        #: host here that refuses 'TGR4' and exits, and this driver would
        #: respawn it and ask again for every utterance, for ever.  Permanent
        #: silence is the worst failure this driver has, so one refusal turns
        #: streaming off and says why, and the old request still works against
        #: every host that has ever existed.
        self._streaming = True
        #: Whether the output stream has been stopped or has run dry, so that
        #: the next chunk fed has to start it again.  Only that one is worth
        #: timing; the rest block because the buffer is full, which is the
        #: point of feeding from its own thread.
        self._playerIdle = True
        #: Wall-clock time the audio handed over so far will finish
        #: playing.  The feeder never runs more than FEED_LEAD past it.
        self._fedUntil = 0.0
        #: Serialises feed() against stop().  NVDA's player changes its stream
        #: state in both without synchronising them, and the two are called
        #: from different threads here -- the feeder and NVDA's main thread.
        self._playerLock = threading.Lock()
        #: Whether the next stream start follows an interruption rather than an
        #: utterance that finished on its own.
        #:
        #: NVDA's feed() waits while the device buffer is more than half full,
        #: and its stop() defers clearing that buffer to the next feed().  A
        #: start measured at 2052 ms is the whole of the remaining lag, and
        #: those two paths would explain it very differently -- so record which
        #: one it was rather than reason about it.
        self._afterCancel = False
        #: How `cancel()` reaches the engine.
        #:
        #: Stopping the sound is instant, but the host went on synthesising the
        #: rest of an utterance nobody would hear, and the worker could not
        #: start the next one until that response ended.  Measured on a real
        #: session: 38% of utterances waited over 200 ms to begin rendering,
        #: the worst 931 ms -- which is the lag people describe, and it
        #: survived streaming untouched because it happens before the first
        #: chunk of the next utterance can exist.
        #:
        #: An event rather than anything on the pipe, because rule 5 stands:
        #: `cancel()` runs on NVDA's main thread and must never block, and
        #: `SetEvent` cannot.  If the event cannot be made, the driver waits
        #: exactly as it used to.
        self._cancelEvent = None
        self._cancelEventName = None
        self._makeCancelEvent()
        self._queue = queue.Queue()
        self._audioQueue = queue.Queue()
        self._player = self._makePlayer()
        self._feeder = threading.Thread(target=self._feed,
                                        name="tigerspeech-feed", daemon=True)
        self._feeder.start()
        self._worker = threading.Thread(target=self._run, name="tigerspeech",
                                        daemon=True)
        self._worker.start()

    # -- plumbing ----------------------------------------------------------
    def _makePlayer(self):
        """Build a WavePlayer across NVDA config generations.

        Each attempt has to be a callable: building the argument dicts up front
        would evaluate every config lookup before the first `try` could catch
        anything.
        """
        import config
        base = dict(channels=1, samplesPerSec=OUT_RATE, bitsPerSample=16)
        try:
            from nvwave import AudioPurpose
            purpose = {"purpose": AudioPurpose.SPEECH}
        except Exception:
            purpose = {}

        def modern():
            return nvwave.WavePlayer(
                outputDevice=config.conf["audio"]["outputDevice"],
                **base, **purpose)

        def legacy():
            return nvwave.WavePlayer(
                outputDevice=config.conf["speech"]["outputDevice"], **base)

        def default():
            return nvwave.WavePlayer(**base, **purpose)

        def bare():
            return nvwave.WavePlayer(1, OUT_RATE, 16)

        last = None
        for attempt in (modern, legacy, default, bare):
            try:
                return attempt()
            except Exception as e:
                last = e
        raise last

    # -- the host ----------------------------------------------------------
    def _restartHost(self):
        """Ask for a fresh engine before the next utterance is sent.

        **Ask, rather than kill.**  Closing the host's stdin from whichever
        thread noticed is how the sibling driver once cut its own stream in
        half; the swap belongs where nothing is in flight, which is `_host()`
        at the top of a render.  Raising a flag is the whole of it.
        """
        self._restartWanted = True

    def _useLibrary(self):
        """-> True when the engine has to be a library rather than a process.

        The rule itself is `dllhost.useLibrary`, shared with the other three
        generations' driver; see there for why it is one question in one place.
        Tiger has no standby host to suppress alongside it -- this driver never
        grew one -- so there is nothing else to say here.
        """
        return dllhost.useLibrary(HOST_EXE, HOST_DLL)

    def _host(self):
        """The resident engine process, started on demand and restarted if it
        dies.  Startup costs about 20 ms including the 2.1 MB dictionary, so a
        restart after a crash is not something the user would notice."""
        with self._procLock:
            if self._restartWanted:
                old, self._proc = self._proc, None
                self._restartWanted = False
                if old is not None:
                    try:
                        old.stdin.close()
                        old.wait(timeout=1)
                    except Exception:
                        try:
                            old.kill()
                        except Exception:
                            pass
            if self._proc is not None and self._proc.poll() is None:
                return self._proc
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            # Follow NVDA's own log level. Someone who has turned debug
            # logging on has asked for detail, and a synthesizer that stays
            # quiet then is no easier to diagnose than one with no logging at
            # all -- the host's commentary is the only view of what the engine
            # is doing. At any other level it says nothing, because it is
            # several hundred lines per utterance.
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
            if dllhost.useLibrary(HOST_EXE, HOST_DLL):
                # No executable to start, so the engine is a library in this
                # process instead -- which is the only way this add-on speaks
                # on a secure screen, where NVDA does not copy the `.exe`.
                # What comes back answers `Popen`'s questions and carries the
                # same two pipes, so nothing below here learns which it got.
                self._proc = dllhost.DllHost(
                    self.name, HOST_DLL, self._mt, self._sd, self._voicesdir,
                    self._cancelEventName if self._cancelEvent else None,
                    ["TIGER_HOST_VERBOSE=%s"
                     % env.get("TIGER_HOST_VERBOSE", "")])
            else:
                self._proc = subprocess.Popen(
                    [HOST_EXE, "--serve", self._mt, self._sd,
                     self._voicesdir],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, startupinfo=si, env=env)
            self._watchStderr(self._proc)
            return self._proc

    def _makeCancelEvent(self):
        """A Windows event the host can watch, named so the child can open it.

        Best effort throughout: every failure here costs responsiveness after
        an interruption and nothing else, so none of it is worth raising over.
        """
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            name = "Local\\tigerspeech-cancel-%d-%d" % (os.getpid(), id(self))
            # Manual reset off, initial state off: the host consumes the signal
            # by waiting on it, and the worker clears any stale one before it
            # sends the next request.
            h = k32.CreateEventW(None, False, False, name)
            if h:
                self._cancelEvent = h
                self._cancelEventName = name
        except Exception as e:
            log.debugWarning("tigerspeech: no cancel event (%s)" % e)

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
                        log.warning("tiger-speech host: %s" % line)
                    else:
                        log.debug("tiger-speech host: %s" % line)
            except Exception:
                pass
            finally:
                try:
                    proc.stderr.close()
                except Exception:
                    pass
        t = threading.Thread(target=pump, name="tigerspeech-host-log")
        t.daemon = True
        t.start()

    def _wpm(self, adj=0):
        """-> words per minute for the slider position.

        The engine has no ceiling worth speaking of -- asked for 1500 wpm it
        delivers 1598 and stays perfectly stable -- so rate boost simply
        raises the top of the slider rather than doing anything clever.

        `adj` is a `RateCommand` offset on NVDA's own 0-100 scale, which is how
        an add-on asks for typing or spelling to be read at a different speed.
        Clamped rather than scaled, like the pitch offset beside it.
        """
        top = RATE_MAX_BOOST if self._rateBoost else RATE_MAX
        rate = min(100, max(0, self._rate + adj))
        return RATE_MIN + int(rate * (top - RATE_MIN) / 100)

    def _pitchOffset(self, adj=0):
        """-> tenths of a semitone away from the voice's own pitch.

        `adj` is what NVDA asked for on top of the user's setting, on its own
        0-100 scale: a PitchCommand carrying the "capital pitch change
        percentage", which is how a capital letter is meant to be marked.  The
        driver used to drop those commands, so that setting did nothing at all
        no matter what it was set to.
        """
        pitch = min(100, max(0, self._pitch + adj))
        return int((pitch - 50) * PITCH_SEMITONES * 10 / 50)

    def _render(self, text, wpm, voice, pitch=0, sink=None, volume=0):
        """-> PCM bytes, or None.  One request, one utterance.

        With a `sink`, the audio is asked for in chunks and each is handed over
        as it arrives, and the return is `b""` because the audio has already
        gone.  A sink returning False stops the feeding without abandoning the
        response.  The engine renders far faster than real time, so the whole
        utterance still arrives in a fraction of its own duration -- what
        changes is that the first of it can be sounding by then.
        """
        text = text.strip()
        if not text:
            return b""
        if not self._acceptCommands:
            # Tiger's front end really does parse "[[rate 100]]", "[[volm 0.5]]"
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
        # **Volume can be said back and inflection cannot**, and the
        # difference is measured rather than guessed.  "[[volm 1.000]]" is a
        # multiplier, and 1.0 is every voice's own level: none of the
        # twenty-four is altered by it.  "[[pmod 100]]" looks like the same
        # move and is not -- pmod is a percentage of a depth that belongs to
        # the voice, and 100 is simply a different number from the one
        # thirteen of the twenty-four were recorded with.  Albert, Bahh,
        # Boing, Cellos, Deranged, Junior, Kathy, Organ, Princess, Trinoids,
        # Vicki and Zarvox are all changed by it and stay changed.
        #
        # This driver has no table of per-voice depths and should not grow
        # one: a channel that has just been opened is at the right depth for
        # whichever voice it is.  So the way back to the default is a new
        # engine, asked for here and acted on by `_host()` a few lines below,
        # before this utterance goes out.  About 20 ms, once, on the single
        # utterance where the slider comes home.
        if self._inflection != 50:
            pmod = int(self._inflection * INFLECTION_MAX_PMOD / 100)
            text = "[[pmod %d]]%s" % (pmod, text)
            self._inflectionSent = True
        elif self._inflectionSent:
            self._restartHost()
            self._inflectionSent = False
        #: `volume` is a VolumeCommand offset on NVDA's 0-100 scale, 0
        #: meaning the user's own setting.  At the default it is 0 and `level`
        #: is `self._volume`, so nothing about an ordinary utterance changes --
        #: which is what keeps Tiger's renders byte-identical.
        level = min(100, max(0, self._volume + volume))
        if level < 100:
            text = "[[volm %.3f]]%s" % (level / 100.0, text)
            self._volumeSent = True
        elif self._volumeSent:
            text = "[[volm 1.000]]%s" % text
            self._volumeSent = False
        try:
            proc = self._host()
            v = voice.encode("utf-8")
            t = _encode(text)
            # A cancel that arrived while nothing was rendering must not be
            # waiting here to kill the utterance that follows it.
            self._clearCancel()
            streaming = sink is not None and self._streaming
            req = REQ_MAGIC_STREAM if streaming else REQ_MAGIC
            proc.stdin.write(struct.pack("<IiiIII", req, wpm, pitch,
                                         0, len(v), len(t)) + v + t)
            proc.stdin.flush()
            if not streaming:
                magic, status, nframes = struct.unpack(
                    "<IiI", _readExactly(proc.stdout, 12))
                if magic != RSP_MAGIC:
                    raise IOError("bad response magic %08x" % magic)
                pcm = _readExactly(proc.stdout, nframes * 2)
                if status:
                    log.debugWarning("tigerspeech: OSErr %d for %r"
                                     % (status, text))
                if sink is not None:
                    # Streaming is off, but the caller still expects its audio
                    # through the sink.  One chunk: the whole utterance, which
                    # is exactly what this driver did before it streamed.
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
                # hear.  The engine renders at about ninety times real time, so
                # draining is cheap.
                if feeding and not sink(chunk):
                    feeding = False
            if status:
                log.debugWarning("tigerspeech: OSErr %d for %r" % (status, text))
            return b""
        except Exception as e:
            # The protocol is a stream: a failed exchange leaves it out of step,
            # so drop the process rather than trying to resynchronise.
            log.debugWarning("tigerspeech: %s" % e)
            with self._procLock:
                if self._proc is not None:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                    self._proc = None
            if sink is not None and self._streaming:
                # A host that does not know 'TGR4' exits rather than answer it,
                # which arrives here as a closed pipe.  Left alone this repeats
                # for every utterance -- respawn, refuse, respawn -- and the
                # user hears nothing at all, ever, which is the worst failure
                # this driver has.  So stop asking, and say so at a level
                # somebody will actually see: a diagnostic nobody can read has
                # not been reported.
                self._streaming = False
                log.warning("tigerspeech: the bundled engine does not "
                            "understand streamed audio, which means its files "
                            "are older than this driver -- reinstall the "
                            "add-on. Speaking the previous way instead.")
            return None

    # -- threads -----------------------------------------------------------
    def _run(self):
        """Render each utterance and hand it on.  Nothing is stamped.

        Reconcile the settings here rather than taking them as queued events:
        `cancel()` drains this queue, and NVDA cancels between changing a
        setting and speaking the confirmation of it, so a queued voice change
        would be eaten and the confirmation spoken in the old voice.
        """
        while not self._stopped:
            item = self._queue.get()
            if item is None:
                break
            wpm, voice = self._wpm(), self._voiceId
            #: What NVDA has asked us to add to the user's pitch for the text
            #: that follows -- how "capital pitch change percentage" is
            #: expressed.  0 means the user's own setting.
            adj = 0
            #: The same again, on the volume slider.
            vol = 0
            run = []
            #: Indexes seen since the last flush, reported once the audio
            #: around them has been handed to the player.
            #:
            #: **An index must not force a split.**  NVDA puts one at the
            #: *start* of every line during say-all -- the `lineReached`
            #: callback -- and it has already decided those lines belong
            #: together: sayAll speaks through `speakWithoutPauses`, which
            #: buffers lines until one of them contains a natural pause.
            #: Splitting at the index undid that decision and handed the engine
            #: a fragment ending in nothing, which it reads as a sentence
            #: ending.
            #:
            #: With word wrap on that is heard as a full stop in the middle of
            #: a sentence -- "narrowing. budgets", "hits. and kills" -- at
            #: exactly the wrapped line boundaries, which is why it sounded so
            #: arbitrary.
            pending = []
            for kind, value in item:
                if self._stopped:
                    break
                if kind == "text":
                    run.append(value)
                    continue
                if kind == "index":
                    pending.append(value)
                    continue
                self._flush(run, wpm, voice, adj, pending, vol)
                if kind == "break":
                    self._audioQueue.put(("audio", _silence(value), self._cancels))
                elif kind == "pitch":
                    adj = value
                elif kind == "volume":
                    vol = value
                elif kind == "rate":
                    # After the flush, never before it: the text already
                    # collected was asked for at the old rate.
                    wpm = self._wpm(value)
            if not self._stopped:
                self._flush(run, wpm, voice, adj, pending, vol)
            for index in pending:               # nothing left to speak
                self._audioQueue.put(("index", index, None))
            del pending[:]
            self._audioQueue.put(("done", None, None))

    def _flush(self, run, wpm, voice, adj=0, pending=None, vol=0):
        """Render the text collected so far as ONE utterance.

        **A speech sequence is not a list of utterances.**  NVDA hands over the
        pieces of a line -- text, a link, more text -- as separate strings, and
        during say-all it hands over several wrapped lines at once, having
        already decided through `speakWithoutPauses` that they belong together.
        Rendering each piece on its own gave every one of them the falling
        intonation and final lengthening of a finished sentence.  Measured, the
        splitting cost 163 ms across two joins, and there is no silence in it
        to trim: the extra is in the speech itself.

        The indexes collected since the last flush are reported immediately
        before this audio, rather than splitting it.  That matches what they
        mean: NVDA's say-all index is the `lineReached` callback, placed at the
        *start* of a line -- "we have just started speaking this" -- and it is
        also what asks for the next line, so reporting it early keeps the
        pipeline fed rather than starving it.  Where several wrapped lines were
        joined, their indexes all arrive at the head of the joined audio, so
        the caret can lead the voice by part of a sentence.  That is the price
        of not putting a full stop in the middle of one.
        """
        if not run:
            if pending:
                for index in pending:
                    self._audioQueue.put(("index", index, None))
                del pending[:]
            return
        text = _joinFragments(run)
        del run[:]
        # The exact string the engine is given.  Reconstructing it from the
        # sequence log is guesswork, and guessing is what has cost the time
        # here: this is the one thing that can be pasted straight into a
        # renderer to reproduce what somebody heard.
        if log.isEnabledFor(log.DEBUG):
            log.debug("tigerspeech: speaking %r" % (text,))
        # Indexes go in before the audio rather than after rendering it.  They
        # belonged at the head of this utterance already -- see the docstring
        # above -- and now that the audio arrives in pieces there is no later
        # moment that would still be the head.
        if pending:
            for index in pending:
                self._audioQueue.put(("index", index, None))
            del pending[:]
        mark = self._cancels
        fed = []
        # What the user actually waits, measured where they wait it.  The two
        # numbers that matter are different questions: how long until the first
        # sound, and how long the whole utterance took to arrive.  Streaming
        # separated them -- before it, they were the same number.
        started = time.perf_counter()
        firstAt = []

        def sink(chunk):
            if self._cancels != mark:
                return False            # interrupted: stop feeding, keep reading
            if not firstAt:
                firstAt.append(time.perf_counter())
            fed.append(len(chunk))
            self._audioQueue.put(("audio", chunk, mark))
            return True

        pcm = self._render(text, wpm, voice, self._pitchOffset(adj),
                           sink=sink, volume=vol)
        if pcm is None and not fed and not self._streaming:
            # That failure was the host refusing to stream, and it has just
            # been turned off.  Say this utterance the old way rather than
            # losing it -- it could be the one telling the user what happened.
            pcm = self._render(text, wpm, voice, self._pitchOffset(adj),
                               sink=sink, volume=vol)
        # Timing, at DEBUG, because "it lags on long text" is the report this
        # driver keeps getting and it was never possible to check from a log.
        # Both numbers, per utterance: a first sound that arrives late is a
        # different fault from an utterance that takes a long time in total.
        if fed and log.isEnabledFor(log.DEBUG):
            done = time.perf_counter()
            frames = sum(fed) / 2.0
            log.debug("tigerspeech: %d chars -> %.2f s of audio in %d chunk(s);"
                      " first sound after %.0f ms, all of it by %.0f ms"
                      % (len(text), frames / OUT_RATE, len(fed),
                         (firstAt[0] - started) * 1000.0,
                         (done - started) * 1000.0))
        if pcm is not None and fed and self._cancels == mark:
            gap = self.PAUSE_MS.get(self._pauseMode, 0)
            if gap:
                self._audioQueue.put(("audio", _silence(gap), mark))

    def _feed(self):
        """Playback lives on its own thread because `feed()` blocks.

        If it ran on the worker, `synthDoneSpeaking` could not be reported
        until the audio had finished sounding, and NVDA would sit waiting.
        """
        while not self._stopped:
            item = self._audioQueue.get()
            if item is None:
                break
            kind, value, tag = item
            # Audio carries the cancel count it was rendered under, and it is
            # checked *here*, after it comes off the queue -- which is the only
            # place a cancel cannot slip past.
            #
            # cancel() bumps the count and then drains this queue; the worker
            # checks the count and then puts.  A chunk landing between those
            # two steps survives the drain and plays against whatever the user
            # asked for next.  With a whole utterance per put that was one
            # narrow window an utterance; streaming made it twenty to seventy,
            # and it started being heard -- a sentence from the post above
            # bleeding into the one below it.
            #
            # This is not the generation stamp rule 3 forbids.  That decided
            # whether to *render*, and losing the race meant silence with
            # nothing to recover it.  This only drops audio the user has
            # already cancelled, and "index" and "done" are never tagged, so
            # NVDA is always told the utterance finished and always asks for
            # the next one.
            if tag is not None and tag != self._cancels:
                continue
            try:
                if kind == "audio":
                    # The last hop, and the only part of the wait this driver
                    # cannot see from the render side.  Feeding the *first*
                    # chunk after the output has been idle or stopped is where
                    # the audio device has to start a stream again, and that
                    # cost belongs to what a user calls lag just as much as the
                    # render does -- so measure it rather than assume it is
                    # small.  Later chunks block on purpose, because the buffer
                    # is full, and timing those would say nothing.
                    # Hand the device roughly real time, not everything at
                    # once.
                    #
                    # Streaming made this urgent: 5.34 s of audio reached the
                    # player inside 363 ms, so an interrupt found seconds of
                    # it already in the sound device with a chunk possibly
                    # mid-feed, and that chunk lands at the head of the next
                    # stream -- heard as a fragment of the post above bleeding
                    # into the start of the post below.  Guarding this queue
                    # cannot catch it, because by then the audio has left us.
                    #
                    # Staying a fraction of a second ahead bounds what an
                    # interrupt can leave behind, and cannot underrun: the
                    # engine renders many times faster than real time.
                    now = time.perf_counter()
                    if self._fedUntil < now:
                        self._fedUntil = now
                    while (self._fedUntil - now > FEED_LEAD
                           and not self._stopped
                           and tag == self._cancels):
                        time.sleep(0.01)
                        now = time.perf_counter()
                    if tag is not None and tag != self._cancels:
                        continue        # interrupted while we waited
                    self._fedUntil = max(self._fedUntil, now) +                         len(value) / 2.0 / OUT_RATE
                    # Serialised against cancel()'s stop().
                    #
                    # NVDA's WASAPI player changes its stream state in both
                    # feed() and stop() without synchronising the two, so a
                    # stop landing while a feed is starting the stream leaves
                    # the next start to stall -- measured at 1839 ms in one
                    # session, which is the "two seconds and you hear nothing"
                    # people reported -- and can let frames from the abandoned
                    # utterance through into the stream that follows.
                    with self._playerLock:
                        if self._playerIdle:
                            self._playerIdle = False
                            t0 = time.perf_counter()
                            self._player.feed(value)
                            ms = (time.perf_counter() - t0) * 1000.0
                            if ms >= 20.0 and log.isEnabledFor(log.DEBUG):
                                log.debug(
                                    "tigerspeech: the audio device took %.0f ms "
                                    "to start playing (%.0f ms of audio, after "
                                    "%s)"
                                    % (ms, len(value) / 2.0 / OUT_RATE * 1000.0,
                                       "an interruption" if self._afterCancel
                                       else "the previous utterance ended"))
                            self._afterCancel = False
                        else:
                            self._player.feed(value)
                elif kind == "index":
                    synthIndexReached.notify(synth=self, index=value)
                elif kind == "done":
                    # idle() waits out the whole utterance, so it must NOT be
                    # held under the player lock: cancel() would then wait for
                    # playback to finish, and cancel must never block.
                    #
                    # The notification goes out whatever the audio device did.
                    # NVDA's speech manager resumes on synthDoneSpeaking, so
                    # losing it to an exception stalls everything queued behind
                    # it -- including the echo of a character just typed.
                    try:
                        self._player.idle()
                    finally:
                        self._playerIdle = True
                        synthDoneSpeaking.notify(synth=self)
            except Exception as e:
                # Never silently: a feed or idle that fails is exactly the
                # failure nobody can account for afterwards.
                log.debugWarning("tigerspeech: feeding audio: %s" % e)

    # -- NVDA interface ----------------------------------------------------
    def speak(self, speechSequence):
        # What NVDA actually sent, when someone has turned debug logging on.
        #
        # Worth having permanently.  Every reported "it pauses in the middle of
        # a sentence" so far has turned out to be about where the sequence was
        # divided, and that is invisible from this side without either a log or
        # a guess.  Two of those guesses were wrong.
        if log.isEnabledFor(log.DEBUG):
            shape = []
            for item in speechSequence:
                if isinstance(item, str):
                    shape.append(repr(item[:200]))
                else:
                    shape.append(type(item).__name__)
            log.debug("tigerspeech: sequence %s" % " | ".join(shape))
        items = []
        for item in speechSequence:
            if isinstance(item, str):
                items.append(("text", item))
            elif isinstance(item, speech.commands.IndexCommand):
                items.append(("index", item.index))
            elif isinstance(item, speech.commands.BreakCommand):
                # NVDA asking for a pause in so many words.  Dropped silently
                # until now, which meant the one place a pause was *wanted*
                # was the one place it did not happen.
                items.append(("break", item.time))
            elif isinstance(item, speech.commands.RateCommand):
                items.append(("rate", item.offset))
            elif isinstance(item, speech.commands.VolumeCommand):
                items.append(("volume", item.offset))
            elif isinstance(item, speech.commands.PitchCommand):
                # How NVDA marks a capital letter: an offset on its own 0-100
                # pitch scale, 0 meaning the user's setting again.  Dropped
                # until now, so "capital pitch change percentage" did nothing
                # whatever it was set to.
                items.append(("pitch", item.offset))
        self._queue.put(items)

    def cancel(self):
        """Discard what is queued and stop what is sounding.

        Runs on NVDA's MAIN thread, which is also the thread that turns typed
        characters into speech, so it must not block: anything slow here stalls
        those events and they arrive in a batch with the next keystroke.  In
        particular it never touches the pipe.

        Draining is the whole mechanism -- see rule 3 at the top.  `stop()` is
        ungated, because a flag that tracked whether the *worker* was busy once
        left interruption silently broken while sound was still playing.
        """
        self._cancels += 1
        # Reach the engine before draining anything: whatever it is rendering
        # now is audio for an utterance already abandoned, and the next one
        # cannot start until that response ends.
        self._signalCancel()
        pendingDone = None
        for q in (self._queue, self._audioQueue):
            while True:
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    break
                if (q is self._audioQueue and isinstance(item, tuple)
                        and item and item[0] == "done"):
                    pendingDone = item
        if pendingDone is not None:
            # Do not throw the completion notice away with the audio.
            #
            # NVDA's speech manager resumes on synthDoneSpeaking, and with the
            # feeder paced this item can sit behind seconds of queued audio --
            # so a cancel was far more likely to swallow it than it used to be,
            # and everything queued behind it waited, including the echo of the
            # character being typed.
            self._audioQueue.put(pendingDone)
        # Stop under the player lock when it is free, so the stop cannot land
        # inside a feed that is starting the stream -- the race that leaves the
        # next start stalling for a second or more.  Never wait long for it:
        # this is NVDA's main thread, and rule 4 says the player is stopped
        # either way.
        held = self._playerLock.acquire(timeout=0.02)
        try:
            self._player.stop()
        except Exception:
            pass
        finally:
            if held:
                self._playerLock.release()
        # Stopping tears the output stream down, so the next chunk pays to
        # start it again -- which is exactly the wait after an interruption
        # that a user feels most sharply.
        self._playerIdle = True
        self._afterCancel = True
        # Nothing is queued at the device any more, so the feeder is not ahead.
        self._fedUntil = 0.0

    def pause(self, switch):
        try:
            self._player.pause(switch)
        except Exception:
            pass

    def terminate(self):
        self._stopped = True
        self.cancel()
        self._queue.put(None)
        self._audioQueue.put(None)
        with self._procLock:
            if self._proc is not None:
                try:
                    self._proc.stdin.close()
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None
        try:
            self._player.close()
        except Exception:
            pass

    # -- settings ----------------------------------------------------------
    def _get_inflection(self):
        return self._inflection

    def _set_inflection(self, value):
        self._inflection = max(0, min(100, int(value)))

    def _get_rateBoost(self):
        return self._rateBoost

    def _set_rateBoost(self, value):
        self._rateBoost = bool(value)

    def _get_volume(self):
        return self._volume

    def _set_volume(self, value):
        self._volume = max(0, min(100, int(value)))

    def _get_acceptCommands(self):
        return self._acceptCommands

    def _set_acceptCommands(self, value):
        self._acceptCommands = bool(value)

    #: How much silence to put where NVDA split the sequence, in milliseconds.
    PAUSE_MS = {"short": 0, "medium": 60, "long": 150}

    def _get_availablePausemodes(self):
        from collections import OrderedDict
        return OrderedDict((
            ("short", StringParameterInfo("short", _("Short"))),
            ("medium", StringParameterInfo("medium", _("Medium"))),
            ("long", StringParameterInfo("long", _("Long"))),
        ))

    def _get_pauseMode(self):
        return self._pauseMode

    def _set_pauseMode(self, value):
        self._pauseMode = value if value in self.PAUSE_MS else "short"

    def _get_pitch(self):
        return self._pitch

    def _set_pitch(self, value):
        self._pitch = max(0, min(100, int(value)))

    def _get_rate(self):
        return self._rate

    def _set_rate(self, value):
        self._rate = max(0, min(100, int(value)))

    def _get_availableVoices(self):
        from collections import OrderedDict
        return OrderedDict(
            (bundle, VoiceInfo(bundle, display, "en"))
            for bundle, display, _engine in self._voices)

    def _get_voice(self):
        return self._voiceId

    def _set_voice(self, value):
        if any(v[0] == value for v in self._voices):
            self._voiceId = value
