# -*- coding: utf-8 -*-
"""The driver body Mac OS X 10.5 and 10.7 share, as native code.

Not a bridge and not an emulator.  `panthera_host.exe` is a 32-bit process
that maps Apple's i386 MacinTalk and SpeechDictionary into itself, fills the
pointer slots dyld would have filled, and calls `SESpeakBuffer` directly.

**This module is not itself a synthesizer.**  NVDA finds drivers by scanning
`synthDrivers/` for modules holding a `SynthDriver` class, and this folder is
deliberately not on that path.  `leopardspeech.py` and `lionspeech.py` are the
drivers; each is a name, a description, a tree module and a table of measured
voice levels on top of `PantheraDriver` below.

It is one body rather than two because Lion's driver differs from Leopard's in
about a hundred and fifty lines out of two thousand three hundred.  This
project has already paid for the alternative twice -- `tiger_host_serve.c` kept
a private copy of the speech calls and drifted from them, and the two test
`conftest.py` files drifted until only one of them recorded what a settings
test read.  A third near-copy of a driver would have drifted the same way, and
one of the copies had already started: Leopard's engine-missing dialog told the
user to run `extract_tiger.py`.

**The host is 32-bit because the engine is i386, and there is no second build
to make**: a 64-bit process cannot load i386 code at all.  Keeping it in its
own process is exactly what makes this add-on indifferent to NVDA's own
bitness -- the same binary serves 32-bit NVDA 2023.1 and 64-bit NVDA 2026.1.
That is the opposite trade-off from the sibling ROM add-on, which loads its
emulator in-process and therefore has to ship one DLL per architecture.

Nothing of Apple's ships here.  The user supplies their own install disc; the
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
   the epoch having moved on, which means the user cancelled -- the one case
   where cutting a word in half is the correct answer.  The rule really being
   kept is the one stated above; the whole-utterance version was the shape it
   took while the audio arrived all at once.

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
import sys
import threading
import time
import queue

import nvwave
import speech.commands
from logHandler import log
from autoSettingsUtils.driverSetting import (BooleanDriverSetting, DriverSetting,
                                             NumericDriverSetting)
from autoSettingsUtils.utils import StringParameterInfo
from synthDriverHandler import (SynthDriver, VoiceInfo, synthDoneSpeaking,
                                synthIndexReached)

#: Re-exported, not merely used: the drivers and the tests reach for
#: these as `pantheradriver.OUT_RATE` and the like, and moving a measured
#: number must not move where it is read from.
from .constants import (  # noqa: F401
    OUT_RATE,
    FEED_LEAD,
    FEED_SLICE,
    RATE_MIN,
    RATE_MAX,
    RATE_MAX_BOOST,
    PITCH_SEMITONES,
    INFLECTION_MAX_PMOD,
    VOLUME_CLEAN,
    VOLUME_MAX_VOLM,
    VOLUME_NORM_CEILING,
    VOLUME_NORM_LEOPARD,
    VOLUME_NORM_DEFAULT,
    volume_volm,
)

#: Re-exported for the same reason the constants are: the tests call
#: `pantheradriver._encode` and `pantheradriver._splitUtterance`, and
#: where a function is defined is not where it is read from.
from .text import (  # noqa: F401
    COMMAND_RE,
    COMMAND_SPLIT_RE,
    INPUT_MODE_RE,
    INPUT_MODE_CAPTURE_RE,
    _FOLD,
    _unmappable,
    _encode,
    SPLIT_MIN,
    SPLIT_FIRST,
    SPLIT_TARGET,
    SPLIT_SLACK,
    _CLOSERS,
    _SENTENCE_END,
    _ABBREVIATIONS,
    _sentenceStarts,
    _PHRASE_MARKS,
    _PHRASE_END,
    _phraseStarts,
    _splitUtterance,
    _joinFragments,
    SENTENCE_END_RE,
    _sentenceEnds,
)

#: Re-exported: the tests reach for `pantheradriver._sliceAudio` and
#: `pantheradriver.SENTENCE_PAUSE_FACTOR`.
from .audio import (  # noqa: F401
    SENTENCE_PAUSE_FACTOR,
    _silence,
    _sliceAudio,
)


def _fullVolumeByDefault(setting):
    """NVDA defaults a numeric driver setting to 50, and volume is one.

    `NumericDriverSetting` takes `defaultVal=50`, and NVDA writes that over
    whatever the driver put in `__init__` -- autoSettings.py does
    `setattr(inst, setting.id, setting.defaultVal)`.  So adding a volume
    control made everybody quieter the moment they upgraded, which is exactly
    what a tester reported: "alex got quieter, not by a whole lot, but it was
    definitely noticeable".

    **90, not 100**, and not because 100 would be too loud. 90 is where each
    voice reaches its own measured maximum: see `VOLUME_NORM`. The last tenth
    of the slider deliberately asks for more than the voice can render
    cleanly, so anyone who wants the loudness more than they mind the
    distortion can have it -- and nobody gets it by accident.

    Eloquence ships 92 for much the same reason, so this is a setting users
    have met before.
    """
    setting.defaultVal = VOLUME_CLEAN
    return setting


# Finding the engine lives in `tree`, not here: the global plugin that offers
# to open the folder needs exactly the same answer, and two copies of a lookup
# is two chances to disagree about where the engine is.
#
# This module used to add its own folder to `sys.path` so it could be imported
# from a command line, and every name in the folder carried a `panthera`
# prefix to survive the flat namespace that created.  Both are gone: it is a
# package now, imported relatively, and the prefix is being retired module by
# module.  What the prefix was defending against is worth keeping written
# down.  Every NVDA add-on shares one `sys.modules`; this driver and its Tiger
# sibling both put their private folder on `sys.path` and both did
# `import tree`, so whichever loaded first won and the second silently got the
# first one's module.  Leopard read tigerspeech-data, ran tiger_host.exe, and
# offered Tiger's twenty-three voices under Leopard's name -- working
# perfectly, and completely wrong.  Nothing failed, which is why it took a
# user noticing the wrong voices to see it.  A relative import cannot reach
# another add-on's folder at all, so the fight cannot start.
#
# Which tree module to use is a property of the *driver*, not of this file:
# Leopard's and Lion's are separate folders holding separate engines, and this
# body serves both.  Each driver names its own as `TREE`, and everything here
# reaches it through `self.TREE`.
from . import bridge
from . import pantheraabbrev
from . import pantheranumbers
from . import pantherastress

#: The host process and the wire to it.  A mixin rather than a base class
#: because it is one subject rather than one layer: `PantheraDriver` is still
#: the synthesizer, and `HostMixin` is only how it reaches the engine.
#:
#: The magics and `_readExactly` come back out by name because the tests
#: exercise the wire format directly against `pantheradriver`.
from .host import (  # noqa: F401
    REQ_MAGIC,
    REQ_MAGIC_STREAM,
    RSP_MAGIC,
    HostMixin,
    _readExactly,
)

#: The worker, the joiner and the feeder.  The join thresholds come back out
#: by name because the tests set them to force a join or forbid one.
from .speech_pipeline import (  # noqa: F401
    JOIN_MAX_CHARS,
    JOIN_MIN_CHARS,
    JOIN_WAIT,
    TUNE_JOIN_MAX_CHARS,
    SpeechPipelineMixin,
)


#: **Named holes, not positional ones.**  The version this replaced had one
#: positional hole for the folder and named the disc and the extractor in
#: the text, so nothing stopped a second generation naming the wrong ones
#: -- and the first copy already had.  Leopard's told the user to run
#: `extract_tiger.py`, which nothing would have caught until somebody ran
#: it against a Leopard disc and got no Alex out of it.
_MISSING = (
    "%(title)s cannot start, because the engine is not there yet.\n\n"
    "This add-on ships no part of Apple's software. You supply it from your "
    "own %(disc)s, and put the extracted Speech folder and "
    "SpeechDictionary.framework into:\n\n"
    "%(folder)s\n\n"
    "The quickest way is NVDA's Tools menu: \"Mac OS X speech data...\", "
    "which reads a disc image straight into that folder and needs nothing "
    "else installed. The %(extractor)s tool in the project repository does "
    "the same job from a command line, and there is a README in that folder "
    "with the details. NVDA's log has the full list of what was found and "
    "what was missing.\n\n"
    "Open that folder now?"
)


def _explainLater(folder, title, disc, extractor):
    """Show the engine-missing dialog once NVDA has finished failing.

    Never straight from `__init__`: a modal dialog there would stall the
    synthesizer switch with speech half torn down. Queued instead, so it
    arrives after NVDA has fallen back to the previous synthesizer and speech
    is working again -- which it always does, so the user is never stranded.

    It lands on top of NVDA's own "Could not load the ... synthesizer" box
    rather than after it, because that box runs a nested event loop which
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
            answer = gui.messageBox(
                _MISSING % {"title": title, "disc": disc,
                            "extractor": extractor, "folder": folder},
                title, wx.YES_NO | wx.ICON_INFORMATION)
            if answer == wx.YES:
                os.makedirs(folder, exist_ok=True)
                os.startfile(folder)
        except Exception:
            log.error("%s: could not show the engine dialog" % title,
                      exc_info=True)
    wx.CallAfter(show)


#: What the host says on every single startup.  Not complaints, and the only


class PantheraDriver(HostMixin, SpeechPipelineMixin, SynthDriver):
    """Everything a generation's driver does, with the generation left out.

    A subclass supplies the seven attributes below and nothing else.  There is
    deliberately no hook for behaviour: where two generations really differ,
    the difference is in the *host*, which reads the engine it was handed and
    picks its own calls.  10.7 moved rate and pitch to `SESetSpeechProperty`
    and nothing up here knows that, which is the shape to keep.
    """

    #: NVDA's identifier for this synthesizer, and the name it keys every
    #: stored setting by.  **It can never change** once released: a rename
    #: silently resets everybody's voice, rate and pitch to the defaults.
    name = None
    #: What NVDA lists in the synthesizer dialog.
    description = None
    #: The module that finds this generation's engine.  Everything here
    #: reaches it through `self.TREE`, so the two generations cannot read each
    #: other's folder -- which is the failure that once had Leopard speaking
    #: in Tiger's voices, working perfectly and completely wrong.
    TREE = None
    #: The generation in running prose, for dialog titles and setting labels.
    TITLE = None
    #: Which disc the user extracts from, named in the missing-engine dialog.
    DISC = None
    #: And which tool does it for them.  Wrong in Leopard's copy of that text
    #: for as long as there was a copy of it to be wrong in.
    EXTRACTOR = None
    #: Per-voice volume normalisation; see `VOLUME_NORM_LEOPARD`.  **Not
    #: shared between generations even where the voice names match**, because
    #: the recordings behind them are not the same recordings: Lion's Alex is
    #: a 422 MB bank where Leopard's is 701 MB.
    VOLUME_NORM = None

    #: Whether `[[inpt PHON]]` and `[[inpt TUNE]]` do what they say on this
    #: generation.  `True` everywhere it has been measured except 10.7; see
    #: `INPUT_MODE_RE` and panthera-speech#6.
    #:
    #: A three-state attribute on purpose -- `None` would be "nobody has
    #: checked", and a generation added later should have to answer the
    #: question rather than inherit somebody else's answer.
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
        # Two settings here put silence into speech and they are not the same
        # thing, so they must not read alike.  This one is *ours*: NVDA hands
        # an announcement over in pieces -- a control's name, then its role,
        # then its state -- and this is the gap the driver puts between those
        # pieces.  It does nothing inside a sentence.
        #
        # It was called "Pause between phrases" and sat next to "Phrase
        # pauses", both on Alt+P.  Tomi: "people will confuse those two.  They
        # will ask me how they differ, and I will have to explain each time."
        DriverSetting(
            "pauseMode",
            _("&Gap between announcement parts"),
            defaultVal="short",
        ),
        # How readily the engine breaks a phrase.  It asks how strong a
        # boundary must be before it earns a silence, and until now was told
        # nothing, so it used its own default.
        #
        # Named values rather than a slider, because a number here is genuinely
        # ambiguous -- Tomi asked it straight: "does setting it to 0 cause
        # phrases to break more often, or 100?  At 0 you're telling the engine
        # don't break up phrases, but 0 could equally mean the setting is off."
        # Both readings are reasonable, so the control says which it means.
        #
        # Measured on Alex at 300 wpm, interior silences:
        #
        #                    "restart with debug   the quoted sentence
        #                     logging enabled"
        #   fewest            1 gap                6 gaps, no 142 ms one
        #   fewer             1 gap                8 gaps
        #   Leopard's own     2 gaps: 191, 94 ms   7 gaps, incl. 142 ms
        #   more / most       1 gap                9 gaps
        #
        # "Fewest" is the default because it is the only setting that is best
        # on every sentence tested -- and note that Leopard's own is the worst
        # of them on the first, which is the complaint this began with.
        DriverSetting(
            "phrasing",
            # The engine's own decision, inside a sentence, about where a
            # clause ends -- nothing to do with the gap setting above.
            _("Engine phrase &breaks"),
            defaultVal="fewest",
            availableInSettingsRing=True,
        ),
        # Where the breathing went, and how to get it back.
        #
        # Alex breathes at a sentence boundary *inside* one utterance and
        # nowhere else -- N sentences give N-1 breaths, measured 0/1/2/5 for
        # 1/2/3/6 sentences.  Reading continuously, NVDA hands over one
        # finished sentence at a time (`speechWithoutPauses` flushes at the
        # last full stop it can find), so there is no boundary inside anything
        # we are given and nothing ever breathes.  Tomi: "it does do it
        # occasionally but not nearly as much as I recall" -- occasionally is
        # when a line happened to carry two full stops.
        #
        # So this holds a finished sentence briefly and speaks it together with
        # the next one.  The cost is that the cursor leads what is being said
        # by up to one extra sentence, which is why it is a setting and not
        # simply the behaviour.
        BooleanDriverSetting(
            "joinSentences",
            # Says the benefit, not the mechanism: nobody wants "coalesce
            # utterances", they want the thing it produces.  T is free -- E, R,
            # G, B and A are taken above.
            _("Brea&the between sentences when reading continuously"),
            defaultVal=True,
        ),
        # The engine reads numbers well up to six digits and gives up at
        # seven -- "3222233" comes out one digit at a time.  It is not tunable:
        # all 283 engine parameters are prosody and unit selection, and number
        # reading lives in SpeechDictionary, which takes no settings.  So the
        # repair is in the text, before the engine sees it.
        DriverSetting(
            "numberStyle",
            _("&Number reading"),
            defaultVal="fix",
        ),
        BooleanDriverSetting(
            "expandAbbreviations",
            # Translators: a synthesizer setting.  The examples are the shapes
            # it covers, and they are there because "abbreviations" alone
            # describes several things the engine does and this is only some
            # of them.
            _("Expand &abbreviations (5KB, 1,234MB, 20ish, DR, ST, XIV)"),
            defaultVal=True,
        ),
        # Alex says "cologne" for "colon" whenever a word follows it, which is
        # every timestamp and every Windows path.  It is de-accenting, not
        # letter-to-sound, and no engine parameter reaches it -- see
        # pantherastress.py for the eleven that were tried.
        #
        # On by default, which is a departure worth being explicit about: this
        # overrides a pronunciation, and normally that is the user's pen.  Two
        # things earn it.  The word that comes out is a *different word* rather
        # than an odd reading of the right one, and nobody ever heard this
        # voice raw -- Alex reached listeners through VoiceOver, with its own
        # symbol handling in front of it.
        #
        # The example in the label is a form that actually fails: "colon" on
        # its own is fine, it is "colon" with something after it that is not.
        BooleanDriverSetting(
            "fixStress",
            _("&Fix words the engine stresses wrongly (colon, as in 3:45)"),
            defaultVal=True,
        ),
    )
    #: **This set is advisory, not a filter.**  NVDA does not strip commands a
    #: driver leaves out of it -- they arrive at `speak()` regardless and are
    #: dropped there, silently, by falling off the end of the loop.  So a
    #: missing entry here is *two* faults that look like one: callers that do
    #: check it (MathCAT does) decline to send the command, and callers that
    #: do not send it and are ignored.
    #:
    #: `RateCommand` was missing until 0.98.1.  Reported by **Amir**, whose
    #: Typing & Spelling Rate add-on wraps every typed character and every
    #: spelt letter in `[RateCommand(offset=N), ..., RateCommand()]` and had no
    #: effect on any Panthera voice at any setting.  NVDA itself never emits
    #: one -- only SSML and add-ons do -- which is exactly why nothing here had
    #: ever noticed.
    supportedCommands = {speech.commands.IndexCommand,
                         speech.commands.BreakCommand,
                         speech.commands.PitchCommand,
                         speech.commands.RateCommand,
                         speech.commands.VolumeCommand}
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
        # Before `super()`, because `super()` is what falls over without it:
        # NVDA's 32-bit bridge host ships a stub `config` with no
        # `pre_configSave`, and `AutoSettings.__init__` registers against it.
        # See `bridge.prepareHost`.
        bridge.prepareHost()
        super().__init__()
        ok, lines = self.TREE.explain()
        if not ok:
            log.warning("%s cannot start:\n  %s"
                        % (self.name, "\n  ".join(lines)))
            _explainLater(self.TREE.config_dir(), self.TITLE, self.DISC,
                          self.EXTRACTOR)
            raise RuntimeError("%s has no engine to run" % self.name)
        self._tree = self.TREE.find_tree()
        if not self._tree:
            raise RuntimeError("no %s tree found" % self.TITLE)
        self._mt, self._sd, self._voicesdir = self.TREE.engine_paths(
            self._tree)
        self._voices = self.TREE.read_voices(self._voicesdir,
                                             playable_only=True)
        if not self._voices:
            raise RuntimeError("no voices in %s" % self._voicesdir)

        self._rate = 50
        self._pitch = 50
        self._acceptCommands = False
        #: The input mode an earlier utterance switched to and never closed
        #: -- "TUNE" or "PHON" -- carried forward so say-all can read a tune
        #: file whose `[[inpt TUNE]]` appears once at the top.  None is text.
        self._inputMode = None
        self._pauseMode = "short"
        self._rateBoost = False
        self._inflection = 50
        self._volume = VOLUME_CLEAN
        #: Both reach the engine through the host's environment, which is read
        #: once at startup, so changing either restarts the host.
        self._phrasing = "fewest"
        self._expandAbbreviations = True
        self._joinSentences = True
        self._numberStyle = "fix"
        #: Unlike the two above, this one is applied to the text in this
        #: process -- nothing is passed to the host -- so changing it takes
        #: effect on the next utterance and needs no restart.
        self._fixStress = True
        #: Whether anything has been spoken since the last cancel.  Joining
        #: never waits for the *first* utterance of a run, so starting to read
        #: is as immediate as it was; from the second on, the next line is
        #: normally queued already and the wait is zero anyway.
        self._spokeSinceCancel = False
        #: Whether a non-default inflection has been sent to the engine and is
        #: still in force on the channel. Volume needs no such flag any more:
        #: it is sent on every utterance, because the level is per-voice and
        #: has to be restated whenever the voice changes anyway.
        self._inflectionSent = False
        self._voiceId = self._voices[0][0]
        # Prefer Alex, then Fred. Alex is the reason this add-on exists, and
        # anyone installing it who has him almost certainly wants him; Fred is
        # the fallback because he is the voice everyone means otherwise.
        for want in ("Alex", "Fred"):
            match = [b for b, _d, _e in self._voices if b == want]
            if match:
                self._voiceId = match[0]
                break

        self._proc = None
        #: An idle host started while a cancelled render is being given its
        #: handoff grace.  If that render does have to be retired, the worker
        #: can move straight onto this process instead of putting a complete
        #: engine start between the keypress and the replacement speech.
        #:
        #: It deliberately has not loaded a voice yet.  Loading Alex merely to
        #: keep him in reserve would touch tens of megabytes of his sample
        #: bank; starting the engine and mapping its dictionaries is the cheap
        #: part we can safely overlap.  One standby is kept after a clean
        #: cancellation so later handoffs do not pay even that much again.
        self._standby = None
        #: Set when an engine setting changes; acted on in _host(), between
        #: utterances, rather than by killing a host that may be mid-stream.
        self._restartWanted = False
        self._procLock = threading.Lock()
        self._stopped = False
        #: Bumped by cancel(). Read by the worker *after* it takes an item off
        #: the queue, and checked again once the render returns -- see _run().
        self._epoch = 0
        #: Whether the bundled host understands a streamed request.
        #:
        #: It ships with this file, so it always should -- and when it did not,
        #: during development, this add-on went completely silent: the host
        #: refuses 'TGR4' and exits, the driver respawns it and asks again, for
        #: every utterance, for ever.  An add-on update whose executable failed
        #: to copy would do the same on a real machine.  One refusal turns
        #: streaming off and says why; the old request works against every host
        #: that has ever existed.
        self._streaming = True
        #: Whether a request is on the pipe with its response still to
        #: come.
        #:
        #: `cancel()` reads it to decide whether there is anything to
        #: take the host away from.  It runs before every spoken
        #: character, and most of those find the engine idle -- retiring
        #: a host that was about to be reused would put its 40 ms
        #: start-up in front of each keystroke, which is the fault this
        #: was written to remove, not one to add.
        self._rendering = False
        #: Which render is in flight, counted rather than flagged.
        #:
        #: **`_rendering` alone cannot answer the question the grace period
        #: asks.**  It is False only between one render and the next, and
        #: during continuous speech that window is microseconds wide -- so a
        #: timer polling it every 10 ms can miss every one of them and reach
        #: its deadline with the flag true, having watched the cancelled
        #: response end and three more begin.  It would then retire a host
        #: that is speaking exactly what the user asked for.
        #:
        #: A counter cannot be missed.  The question is "is the render I was
        #: started for still the one in flight", and that is what this
        #: answers.
        self._renderSeq = 0
        #: Whether a retirement is already under way, so that a burst of
        #: cancels starts one thread rather than one per keystroke.
        self._retiring = False
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
        #: Stopping the sound is instant, but the host went on synthesising
        #: the rest of an utterance nobody would hear, and the worker could
        #: not begin the next one until that response ended.  Measured here:
        #: interrupting a paragraph of Alex cost 2255 ms before the next
        #: utterance was heard.  Streaming never touched it, because it
        #: happens before the next utterance's first chunk can exist.
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
                                        name=self.name + "-feed", daemon=True)
        self._feeder.start()
        self._worker = threading.Thread(target=self._run, name=self.name,
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


    def _wpm(self, adj=0):
        """-> words per minute for the slider position.

        The engine has no ceiling worth speaking of -- asked for 1500 wpm it
        delivers 1598 and stays perfectly stable -- so rate boost simply
        raises the top of the slider rather than doing anything clever.

        `adj` is what NVDA asked for on top of the user's setting, on its own
        0-100 rate scale -- a `RateCommand`, which is how an add-on asks for
        typing or spelling to be read at a different speed.  Clamped rather
        than scaled, exactly like the pitch offset beside it, so an add-on
        asking for +100 gets the top of the slider rather than an error.
        """
        top = RATE_MAX_BOOST if self._rateBoost else RATE_MAX
        rate = min(100, max(0, self._rate + adj))
        return RATE_MIN + int(rate * (top - RATE_MIN) / 100)

    def _pitchOffset(self, adj=0):
        """-> tenths of a semitone away from the voice's own pitch.

        `adj` is what NVDA asked for on top of the user's setting, on its
        own 0-100 scale: a PitchCommand carrying the "capital pitch change
        percentage", which is how a capital letter is meant to be marked.
        The driver used to drop those commands, so that setting did
        nothing at all no matter what it was set to.
        """
        pitch = min(100, max(0, self._pitch + adj))
        return int((pitch - 50) * PITCH_SEMITONES * 10 / 50)


    # -- NVDA interface ----------------------------------------------------
    def speak(self, speechSequence):
        # What NVDA actually sent, when someone has turned debug logging on.
        #
        # Worth having permanently.  Every reported "it pauses in the middle
        # of a sentence" so far has been about where the sequence was divided
        # or what was in it, and that is invisible from this side without
        # either a log or a guess.
        if log.isEnabledFor(log.DEBUG):
            shape = []
            for item in speechSequence:
                if isinstance(item, str):
                    shape.append(repr(item[:200]))
                else:
                    shape.append(type(item).__name__)
            log.debug("%s: " % self.name + "sequence %s" % " | ".join(shape))
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
                # How NVDA marks a capital letter: an offset on its own
                # 0-100 pitch scale, 0 meaning the user's setting again.
                # Dropped until now, so "capital pitch change percentage"
                # did nothing whatever it was set to.
                items.append(("pitch", item.offset))
            elif isinstance(item, speech.commands.VolumeCommand):
                # The third of the same shape.  Nothing has asked for this one
                # yet, but the sibling ROM driver has accepted it since its own
                # sequence work and this one had quietly fallen behind -- which
                # is the drift that left the rate command missing at all.
                items.append(("volume", item.offset))
            elif isinstance(item, speech.commands.RateCommand):
                # The same shape as the pitch command, on the rate slider,
                # and dropped here for the same reason for longer.  NVDA
                # never sends one itself, so only an add-on or SSML ever
                # asks -- which is why this was reported from outside
                # rather than found from inside.
                items.append(("rate", item.offset))
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
        self._epoch += 1
        #: Whatever was being held for joining belongs to the run that has just
        #: been cancelled, and the next utterance must not wait behind it.
        self._spokeSinceCancel = False
        #: And so does an input mode a cancelled run left open: the next
        #: thing spoken is the user doing something else, not verse five.
        self._inputMode = None
        # Reach the engine before draining anything: whatever it is rendering
        # now is audio for an utterance already abandoned, and the next one
        # cannot start until that response ends.
        self._signalCancel()
        # And take the host away from what it is rendering.  The signal
        # alone only stops the audio; the response still takes as long as
        # the whole utterance would have, and the worker is stuck reading
        # it -- see _abandonHost(), which measures both.  Nothing happens
        # unless something really is rendering, and the work itself is on
        # its own thread, so this stays off NVDA's main thread as rule 5
        # requires.
        self._abandonHost()
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
        #
        # So time it, end to end, and let the driver say when it was slow.
        # Everything between here and the next sound is already logged in
        # pieces -- the render, the device start, the handoff -- and every one
        # of those pieces has measured fast while a user still heard seconds.
        # A stall nobody can see in a log is a stall nobody can fix, and the
        # pieces summing to less than the whole is itself the finding.
        self._cancelledAt = time.perf_counter()
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
            proc, self._proc = self._proc, None
            standby, self._standby = self._standby, None
        self._stopHost(proc, graceful=True)
        self._stopHost(standby, graceful=True)
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


    #: What each choice tells the engine.  `None` means it is told nothing,
    #: which is not the same as being told a number: unanswered is Leopard's
    #: own model, and it sits in the *middle* of what can be asked for.  The
    #: first version of this ran 0 to 8 and was therefore entirely on the side
    #: of more breaking than Leopard does by itself, which is why every
    #: position sounded busier than the default.  Negative values are where
    #: "fewer" lives, and it saturates by -10: -100 renders identically.
    #: **Measured 2026-08-19**, seventeen thresholds against three texts.  The
    #: parameter does not respond smoothly, and there are far fewer distinct
    #: behaviours than there are numbers:
    #:
    #:   "Restart with debug logging enabled"   every value from -20 to +8 is
    #:                                          BYTE-IDENTICAL.  Only unanswered
    #:                                          differs -- 2.06 s against
    #:                                          1.54 s, with 229 ms and 120 ms
    #:                                          breaks that fence "logging".
    #:   the news paragraph        -20/-10/-8 | -5/-4 | -3..3 | 4/5/6/8
    #:   three short sentences     -20..-3    | -2..4 | 5/6/8
    #:
    #: The old ladder put -2.0 and +2.0 both inside the -3..3 class, so "fewer"
    #: and "more" rendered **byte-identical** -- two of five positions doing
    #: exactly the same thing.  That is what Tomi heard as "most doesn't bring
    #: it back up like Leopard original does".
    #:
    #: Two values count as the same behaviour only when they are identical on
    #: *every* text, which leaves six numeric classes to choose from, not the
    #: three a stricter reading suggests.  These four are pairwise
    #: byte-distinct, and the pause count rises across them on both long texts:
    #:
    #:                    news paragraph   three sentences
    #:   fewest  -8             14 gaps         11
    #:   fewer   -4             19              11
    #:   more     0             25              14
    #:   most     5             33              17
    #:
    #: Frames rise with them too (348230 / 370830 / 392056 / 427597), so the
    #: ordering is not an artefact of how a gap is counted.  `-8` rather than
    #: `-10` and `-4` rather than `-5` only because they are the same bytes and
    #: sit closer to the live range; `0` is the value already confirmed by ear.
    #:
    #: **Leopard's own is deliberately last, off the end of that ladder.** It
    #: is not a threshold, it is the parameter left unanswered, and it has no
    #: fixed rank: on the paragraph it falls between `fewest` and `fewer`, on
    #: three sentences between `fewer` and `more`, and on a short phrase it is
    #: far above all of them -- the only position that puts 229 ms and 120 ms
    #: breaks inside "Restart with debug logging enabled", which is the
    #: complaint this whole setting began with.
    PHRASING = {
        "fewest":   -8.0,
        "fewer":    -4.0,
        "more":      0.0,
        "most":      5.0,
        "leopard":  None,
    }

    def _get_availablePhrasings(self):
        from collections import OrderedDict
        return OrderedDict((
            ("fewest", StringParameterInfo("fewest", _("Fewest pauses"))),
            ("fewer", StringParameterInfo("fewer", _("Fewer pauses"))),
            ("more", StringParameterInfo("more", _("More pauses"))),
            ("most", StringParameterInfo("most", _("Most pauses"))),
            #: The stored value stays `leopard` on both generations.  It is the
            #: key Leopard has been writing into people's
            #: configuration since 0.7, and an unrecognised one here does
            #: not fall back to "leave it alone" -- `_phrasingParam` falls
            #: back to a threshold -- so renaming it would quietly change
            #: how everybody's speech is phrased.
            ("leopard", StringParameterInfo(
                "leopard",
                # Translators: a phrasing choice, meaning leave the
                # engine's own pause threshold alone.  %s is the
                # generation, for example "Leopard" or "Lion".
                _("%s's own") % self.TITLE)),
        ))

    def _phrasingParam(self):
        """-> the TIGER_PARAMS value for the current choice, or None."""
        threshold = self.PHRASING.get(self._phrasing, -10.0)
        if threshold is None:
            return None
        return "Boundaries.SilThreshold=%g" % threshold

    def _get_phrasing(self):
        return self._phrasing

    def _set_phrasing(self, value):
        value = value if value in self.PHRASING else "fewest"
        if value != self._phrasing:
            self._logSettingChange("phrasing", self._phrasing, value)
            self._phrasing = value
            self._restartHost()

    def _logSettingChange(self, name, was, now):
        """Say who changed an engine setting, and to what.

        NVDA applies a checkbox to the driver the moment it is ticked, but
        writes it to configuration only when the dialog is saved -- and
        `loadSettings(onlyChanged=True)` re-applies the *configured* value on
        every config-profile switch (`synthDriverHandler.py:584`), with a full
        reload on Cancel (`settingsDialogs.py:1586`).  So an unsaved toggle can
        be reverted by something the user never associated with the setting,
        which is exactly the "sometimes it sticks, sometimes it doesn't" this
        line exists to prove or disprove.

        The caller is what distinguishes the two: a change from the dialog
        arrives under `_onCheckChanged`, a revert under `_loadSpecificSettings`.
        """
        try:
            import traceback
            # Our own frames, so the first one that is not ours is the caller
            # worth naming.  Two files now, not one: this body and the driver
            # module that subclasses it.
            mine = {os.path.abspath(__file__)}
            try:
                mine.add(os.path.abspath(
                    sys.modules[type(self).__module__].__file__))
            except Exception:
                pass
            who = "?"
            for frame in reversed(traceback.extract_stack()[:-2]):
                if os.path.abspath(frame.filename) not in mine:
                    who = "%s:%d %s" % (os.path.basename(frame.filename),
                                        frame.lineno, frame.name)
                    break
            # The driver's identity matters: NVDA can hold more than one
            # instance alive, and a checkbox bound to a retired one would
            # change a setting nothing is speaking through -- which looks
            # exactly like "sometimes it does not stick".
            log.debug("%s: " % self.name + "%s %r -> %r on driver %#x, from %s"
                      % (name, was, now, id(self), who))
        except Exception:
            pass

    def _get_expandAbbreviations(self):
        return self._expandAbbreviations

    def _set_expandAbbreviations(self, value):
        value = bool(value)
        if value != self._expandAbbreviations:
            self._logSettingChange("expandAbbreviations",
                                   self._expandAbbreviations, value)
            self._expandAbbreviations = value
            self._restartHost()

    def _get_fixStress(self):
        return self._fixStress

    def _set_fixStress(self, value):
        value = bool(value)
        if value != self._fixStress:
            self._logSettingChange("fixStress", self._fixStress, value)
            self._fixStress = value
            # No _restartHost: the respelling happens here, not in the host.

    def _get_availableNumberstyles(self):
        from collections import OrderedDict
        return OrderedDict((
            ("off", StringParameterInfo(
                "off",
                # Translators: a number-reading choice, meaning leave the
                # engine to read numbers its own way.  %s is the
                # generation, for example "Leopard" or "Lion".
                _("%s's own") % self.TITLE)),
            # What is actually broken, and nothing else: seven digits and up
            # get their separators back, because the engine reads "1,234,567"
            # correctly and keeps its own phrasing that way; and a leading zero
            # is written out, because "0.7.3" loses it altogether.
            ("fix", StringParameterInfo("fix", _("Fix long numbers"))),
            ("words", StringParameterInfo("words", _("All numbers as words"))),
        ))

    def _get_numberStyle(self):
        return self._numberStyle

    def _set_numberStyle(self, value):
        #: Text is rewritten per utterance, so this needs no host restart and
        #: takes effect on the very next thing spoken.
        value = value if value in ("off", "fix", "words") else "fix"
        if value != self._numberStyle:
            self._logSettingChange("numberStyle", self._numberStyle, value)
            self._numberStyle = value

    def _get_joinSentences(self):
        return self._joinSentences

    def _set_joinSentences(self, value):
        #: Nothing to restart: this only changes how the worker groups what is
        #: already queued, so it takes effect on the very next utterance.
        value = bool(value)
        if value != self._joinSentences:
            self._logSettingChange("joinSentences", self._joinSentences, value)
            self._joinSentences = value

    #: How much silence to put where NVDA split the sequence, in milliseconds.
    PAUSE_MS = {"short": 0, "medium": 60, "long": 150}

    #: And how much of the engine's own composed sentence pause to restore
    #: between continuous-reading chunks, on the same setting -- one control,
    #: because a person reaching for "shorter gaps" means all of them, and a
    #: pause the setting cannot touch reads as the setting doing nothing.
    #: "Long" is the engine's own measured length exactly
    #: (`SENTENCE_PAUSE_FACTOR`); reported by Tomi, who set Short and could
    #: still hear the full 0.4 s.
    PAUSE_SCALE = {"short": 0.4, "medium": 0.7, "long": 1.0}

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

    def _getAvailableVoices(self):
        """The voice list, under the name NVDA's own base class declares.

        **There are two spellings of this and they are not interchangeable.**
        `_getAvailableVoices` is the extension point -- `SynthDriver` defines
        it and raises `NotImplementedError` -- while `_get_availableVoices` is
        a wrapper around it that caches the result.  Overriding only the
        wrapper works perfectly in NVDA itself, which reads the property, and
        fails through the 32-bit bridge, whose service calls
        `self._synth._getAvailableVoices()` directly and so reaches the base
        class's refusal instead of us.

        Measured on a sign-in screen: the driver loaded, every setting crossed
        the bridge, the voice read back as "Alex", and then this raised
        `NotImplementedError` with nothing else wrong.
        """
        from collections import OrderedDict
        return OrderedDict(
            (bundle, VoiceInfo(bundle, display, "en"))
            for bundle, display, _engine in self._voices)

    def _get_availableVoices(self):
        """Kept, and deliberately not left to the base class's cache.

        NVDA's wrapper remembers the first answer for the life of the driver.
        Nothing here changes the voice list once it is running, so the cache
        would be harmless -- but this is the accessor NVDA has always called,
        and leaving it in place means the desktop path is not touched at all
        by a change made for a screen it never uses.
        """
        return self._getAvailableVoices()

    def _get_voice(self):
        return self._voiceId

    def _set_voice(self, value):
        if any(v[0] == value for v in self._voices):
            self._voiceId = value
