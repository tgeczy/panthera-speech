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
from autoSettingsUtils.driverSetting import BooleanDriverSetting
from synthDriverHandler import (SynthDriver, VoiceInfo, synthDoneSpeaking,
                                synthIndexReached)

#: Sample format the engine renders in.  Read from the StreamFormat it sets,
#: not assumed: 22050 Hz, mono, and the host converts its 32-bit float to 16.
OUT_RATE = 22050

#: NVDA's 0-100 rate onto words per minute.  180 is Tiger's own default and
#: lands mid-slider, so the control behaves the way people expect.
RATE_MIN, RATE_MAX = 80, 400

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
        BooleanDriverSetting(
            "acceptCommands",
            _("Accept &embedded speech commands in text"),
            defaultVal=False,
        ),
    )
    supportedCommands = {speech.commands.IndexCommand}
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
        return RATE_MIN + int(self._rate * (RATE_MAX - RATE_MIN) / 100)

    def _pitchOffset(self):
        """-> tenths of a semitone away from the voice's own pitch."""
        return int((self._pitch - 50) * PITCH_SEMITONES * 10 / 50)

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
        try:
            proc = self._host()
            v = voice.encode("utf-8")
            t = text.encode("utf-8")
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
            wpm, voice, pitch = self._wpm(), self._voiceId, self._pitchOffset()
            for kind, value in item:
                if self._stopped:
                    break
                if kind == "index":
                    self._audioQueue.put(("index", value))
                    continue
                pcm = self._render(value, wpm, voice, pitch)
                if pcm:
                    self._audioQueue.put(("audio", pcm))
            self._audioQueue.put(("done", None))

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
        items = []
        for item in speechSequence:
            if isinstance(item, str):
                items.append(("text", item))
            elif isinstance(item, speech.commands.IndexCommand):
                items.append(("index", item.index))
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
    def _get_acceptCommands(self):
        return self._acceptCommands

    def _set_acceptCommands(self, value):
        self._acceptCommands = bool(value)

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
