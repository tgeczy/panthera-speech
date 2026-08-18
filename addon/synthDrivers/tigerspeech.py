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

2. *Hand the player a whole utterance at a time.*  Slicing it into chunks
   created a holding area where rendered audio waited to be discarded --
   measured, 367 of 435 utterances thrown away in one session, heard as words
   cut in half.

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
import sys
import subprocess
import threading
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
RATE_MAX_BOOST = 800

#: NVDA's 0-100 pitch onto an offset from the voice's own pitch, in tenths of
#: a semitone.  50 is the voice as Apple recorded it; the ends are an octave
#: either way, which is as far as any of these stay recognisable.
#:
#: An offset rather than an absolute value because every voice has its own
#: natural pitch -- Fred sits near 127 Hz, Bruce near 135 -- so an absolute
#: scale would make the middle of the slider mean something different for each.
#: The host asks the engine for the voice's own 'pbas' and adds this to it.
PITCH_SEMITONES = 12

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
RSP_MAGIC = 0x54475253          # 'TGRS'

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.join(_HERE, "_tigerspeech")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

# Finding the engine lives in `tree`, not here: the global plugin that offers
# to open the folder needs exactly the same answer, and two copies of a lookup
# is two chances to disagree about where the engine is.
import tree                                                   # noqa: E402

HOST_EXE = tree.HOST_EXE
find_tree = tree.find_tree
engine_paths = tree.engine_paths
read_voices = tree.read_voices
config_base = tree.config_base


_MISSING = (
    "Tiger-speech cannot start, because the engine is not there yet.\n\n"
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


def _explainLater(folder):
    """Show the engine-missing dialog once NVDA has finished failing.

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
            answer = gui.messageBox(_MISSING % folder, "Tiger-speech",
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
    description = _("Tiger-speech (MacinTalk 3.3)")

    supportedSettings = (
        SynthDriver.VoiceSetting(),
        SynthDriver.RateSetting(),
        SynthDriver.PitchSetting(),
        _fullVolumeByDefault(SynthDriver.VolumeSetting()),
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
    supportedCommands = {speech.commands.IndexCommand,
                         speech.commands.BreakCommand,
                         speech.commands.PitchCommand}
    supportedNotifications = {synthIndexReached, synthDoneSpeaking}

    @classmethod
    def check(cls):
        """Always offer the synthesizer, and explain on selection if it cannot
        run.

        This used to hide itself when the engine was missing, reasoning that a
        synthesizer which is selectable and then silent is worse than one that
        is absent. That is still true -- but it describes a driver that *loads*
        and then produces no audio, which was the 32-bit DLL case. It does not
        describe one that refuses to load: NVDA catches the failure, falls back
        to the previous synthesizer, and speech never stops.

        What hiding did cost was every route to an explanation. People
        installed the add-on, extracted the engine, found nothing in the
        synthesizer list, and had nothing to go on. The start-up dialog was
        supposed to cover that, and on at least one machine it never appears.

        So be present and say why. Selecting it now fails cleanly and puts up a
        dialog naming the folder the engine belongs in, every time, with no
        dependence on catching the user during start-up.
        """
        return True

    def __init__(self):
        super().__init__()
        ok, lines = tree.explain()
        if not ok:
            log.warning("tiger-speech cannot start:\n  %s" % "\n  ".join(lines))
            _explainLater(tree.config_dir())
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
        self._volume = 100
        self._voiceId = self._voices[0][0]
        for bundle, _display, engine in self._voices:      # prefer Fred
            if bundle == "Fred":
                self._voiceId = bundle
                break

        self._proc = None
        self._procLock = threading.Lock()
        self._stopped = False
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
    def _host(self):
        """The resident engine process, started on demand and restarted if it
        dies.  Startup costs about 20 ms including the 2.1 MB dictionary, so a
        restart after a crash is not something the user would notice."""
        with self._procLock:
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
            self._proc = subprocess.Popen(
                [HOST_EXE, "--serve", self._mt, self._sd, self._voicesdir],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, startupinfo=si, env=env)
            self._watchStderr(self._proc)
            return self._proc

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

    def _wpm(self):
        """-> words per minute for the slider position.

        The engine has no ceiling worth speaking of -- asked for 1500 wpm it
        delivers 1598 and stays perfectly stable -- so rate boost simply
        raises the top of the slider rather than doing anything clever.
        """
        top = RATE_MAX_BOOST if self._rateBoost else RATE_MAX
        return RATE_MIN + int(self._rate * (top - RATE_MIN) / 100)

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

    def _render(self, text, wpm, voice, pitch=0):
        """-> PCM bytes, or None.  One request, one utterance."""
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
        if self._volume < 100:
            text = "[[volm %.3f]]%s" % (self._volume / 100.0, text)
        try:
            proc = self._host()
            v = voice.encode("utf-8")
            t = _encode(text)
            proc.stdin.write(struct.pack("<IiiIII", REQ_MAGIC, wpm, pitch,
                                         0, len(v), len(t)) + v + t)
            proc.stdin.flush()
            head = proc.stdout.read(12)
            if len(head) < 12:
                raise IOError("engine closed the pipe")
            magic, status, nframes = struct.unpack("<IiI", head)
            if magic != RSP_MAGIC:
                raise IOError("bad response magic %08x" % magic)
            want = nframes * 2
            pcm = b""
            while len(pcm) < want:          # a pipe read can come up short
                chunk = proc.stdout.read(want - len(pcm))
                if not chunk:
                    raise IOError("truncated audio")
                pcm += chunk
            if status:
                log.debugWarning("tigerspeech: OSErr %d for %r" % (status, text))
            return pcm
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
                self._flush(run, wpm, voice, adj, pending)
                if kind == "break":
                    self._audioQueue.put(("audio", _silence(value)))
                elif kind == "pitch":
                    adj = value
            if not self._stopped:
                self._flush(run, wpm, voice, adj, pending)
            for index in pending:               # nothing left to speak
                self._audioQueue.put(("index", index))
            del pending[:]
            self._audioQueue.put(("done", None))

    def _flush(self, run, wpm, voice, adj=0, pending=None):
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
                    self._audioQueue.put(("index", index))
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
        pcm = self._render(text, wpm, voice, self._pitchOffset(adj))
        if pending:
            for index in pending:
                self._audioQueue.put(("index", index))
            del pending[:]
        if pcm:
            self._audioQueue.put(("audio", pcm))
            gap = self.PAUSE_MS.get(self._pauseMode, 0)
            if gap:
                self._audioQueue.put(("audio", _silence(gap)))

    def _feed(self):
        """Playback lives on its own thread because `feed()` blocks.

        If it ran on the worker, `synthDoneSpeaking` could not be reported
        until the audio had finished sounding, and NVDA would sit waiting.
        """
        while not self._stopped:
            item = self._audioQueue.get()
            if item is None:
                break
            kind, value = item
            try:
                if kind == "audio":
                    self._player.feed(value)
                elif kind == "index":
                    synthIndexReached.notify(synth=self, index=value)
                elif kind == "done":
                    self._player.idle()
                    synthDoneSpeaking.notify(synth=self)
            except Exception:
                pass

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
        for q in (self._queue, self._audioQueue):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        try:
            self._player.stop()
        except Exception:
            pass

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
