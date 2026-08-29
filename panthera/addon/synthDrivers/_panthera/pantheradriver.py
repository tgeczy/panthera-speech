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


#: How long to hold a finished sentence hoping the next one arrives.  Only ever
#: waited once speech is already flowing, so it delays the start of nothing --
#: and by then the next line is usually queued already, making the wait zero.
JOIN_WAIT = 0.35

#: And how much text has to be in hand before the hold is allowed at all.
#:
#: **A full stop is not enough to call something a line of a document**, which
#: is what the hold assumed.  Reported by ear: "Browse...  button  Alt+  b"
#: arrived a third of a second late, and so did "Home. 3 of 2724" and
#: "Notifications. 24 of 1870", while "12345" was instant.  That reads as a
#: punctuation bug in the engine and is nothing of the sort -- every one of
#: those announcements ends a sentence as far as `_sentenceEnds` is concerned,
#: so every one of them was held for `JOIN_WAIT` waiting for a next sentence
#: that was never coming.  Measured: 359.5 ms against 0.1 ms.
#:
#: A control's name with a trailing "..." is a user interface convention, not
#: prose, and "Home. 3 of 2724" is a list position.  Neither is a sentence
#: anybody wants a breath after, and the hold buys nothing on either -- a
#: breath needs a boundary *inside* one utterance, and two fragments this
#: short do not make one worth waiting for.
#:
#: 60 is `SPLIT_MIN`, which already draws this line for the other direction:
#: below it, text is "a word, a control type, a state" and is never split.
#: The same number for the same reason.
JOIN_MIN_CHARS = SPLIT_MIN
#: And never hold more than this, however the text is punctuated: a page with
#: no full stop in it must not accumulate until the reader notices.
JOIN_MAX_CHARS = 800
#: The cap while an input mode is carried, replacing both prose bounds: a
#: tune's prosodic "." and "!" phonemes count as sentence ends, so the
#: two-sentence rule split a song into verse-sized utterances and the engine
#: gave every verse an utterance-final pitch fall -- the last note of each
#: chunk drooped (panthera-speech#11).  Whole-buffer rendering is the only
#: reference ever validated against a real Mac, so mid-song the joiner holds
#: until the song ends, the mode closes, or this cap.  Bounded because an
#: unbounded hold is memory and pathology exposure for zero benefit: a song
#: longer than this joins in eight-kilobyte movements and takes one fall per
#: movement instead of one per verse.
TUNE_JOIN_MAX_CHARS = 8000


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


class PantheraDriver(HostMixin, SynthDriver):
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


    # -- threads -----------------------------------------------------------
    def _run(self):
        """Render each utterance and hand it on.

        Reconcile the settings here rather than taking them as queued events:
        `cancel()` drains this queue, and NVDA cancels between changing a
        setting and speaking the confirmation of it, so a queued voice change
        would be eaten and the confirmation spoken in the old voice.

        **The epoch check is why an utterance is not spoken after it was
        cancelled.** `cancel()` empties both queues, but it cannot reach the
        one utterance already being rendered, and that render blocks: Alex
        takes about 185 ms for a file name, against 15 to 40 for Fred. Arrowing
        down a list faster than that, every item you hear is the previous one,
        which sounds like the synthesizer reading the same thing over and over.

        The stamp is taken **after** the item is dequeued, not when it was
        queued. That distinction matters: an earlier attempt stamped at queue
        time and compared at render time, so a cancel arriving anywhere in that
        window made an item stale, and it reached a state where every item was
        stale and the synthesizer never spoke again. Here an item is dropped
        only if a cancel arrived during its own render -- so once the cancels
        stop, the very next item is spoken.
        """
        while not self._stopped:
            item = self._queue.get()
            if item is None:
                break
            epoch = self._epoch
            #: Read before `_join`, which strips the indexes it reports early:
            #: an index is the continuous-reading signal, and this item being
            #: part of a continuous read is what earns it the restored
            #: sentence pause below.
            #:
            #: The second half is the joiner's own lesson, re-learned here by
            #: ear: **an index alone does not mean a document.**  A list item
            #: reached by first letter can carry one too, and giving it a
            #: trailing sentence pause read as first-letter navigation
            #: growing a 0.4 s gap.  A say-all chunk ends at a full stop and
            #: has a sentence's length -- `speakWithoutPauses` flushes at the
            #: last full stop it can find -- so a short or unpunctuated thing
            #: carrying an index is an announcement, and announcements end
            #: the way they always have.
            _chunkText = _joinFragments(
                [v for k, v in item if k == "text"])
            continuous = (any(k == "index" for k, _ in item)
                          and _sentenceEnds(_chunkText) >= 1
                          and len(_chunkText) >= JOIN_MIN_CHARS)
            item = self._join(item, epoch)
            wpm, voice = self._wpm(), self._voiceId
            #: What NVDA has asked us to add to the user's pitch for the
            #: text that follows -- how "capital pitch change percentage"
            #: is expressed.  0 means the user's own setting.
            adj = 0
            #: The same again, on the volume slider.
            vol = 0
            run = []
            #: Indexes seen since the last flush.
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
            #: exactly the wrapped line boundaries.
            pending = []
            for kind, value in item:
                if self._stopped or self._epoch != epoch:
                    break
                if kind == "text":
                    run.append(value)
                    continue
                if kind == "index":
                    pending.append(value)
                    continue
                self._flush(run, wpm, voice, adj, epoch, pending, vol)
                if kind == "break":
                    self._audioQueue.put(("audio", _silence(value), self._epoch))
                elif kind == "pitch":
                    adj = value
                elif kind == "volume":
                    vol = value
                elif kind == "rate":
                    # After the flush above, never before it: the text already
                    # collected was asked for at the old rate, and applying a
                    # change backwards over it is how "the first word comes out
                    # at the wrong speed" happens.
                    wpm = self._wpm(value)
            if not self._stopped and self._epoch == epoch:
                self._flush(run, wpm, voice, adj, epoch, pending, vol)
                if continuous and self._inputMode is None:
                    #: **The engine's own sentence pause, restored between
                    #: chunks.**  Inside one utterance the engine composes
                    #: about half a second between sentences at 180 wpm; a
                    #: chunk ends with zero trailing frames, so during
                    #: continuous reading every chunk boundary slammed shut
                    #: (panthera-speech#10).  The length is the engine's own,
                    #: measured -- `SENTENCE_PAUSE_FACTOR / wpm`, one law for
                    #: every voice and generation -- scaled by the same gap
                    #: setting that governs announcement parts, so "Short"
                    #: is audibly short and "Long" is the engine exactly.
                    #:
                    #: Never while an input mode is being carried:
                    #: `_inputMode` here is the mode in force *after* this
                    #: utterance rendered, so mid-song a chunk boundary is a
                    #: bar line, not a paragraph -- a tune's prosodic "." and
                    #: "!" phonemes count as sentence ends, and the reported
                    #: song gained a half-second rest per verse the day the
                    #: pause shipped.  The chunk that closes the song with
                    #: `[[inpt TEXT]]` leaves the mode None and pauses like
                    #: the prose it returns to.
                    pause = _silence(
                        SENTENCE_PAUSE_FACTOR / max(1, wpm)
                        * self.PAUSE_SCALE.get(self._pauseMode, 1.0))
                    if pause:
                        self._audioQueue.put(("audio", pause, self._epoch))
            for index in pending:               # nothing left to speak
                self._audioQueue.put(("index", index, None))
            del pending[:]
            self._spokeSinceCancel = True
            self._audioQueue.put(("done", None, None))

    def _reportIndexes(self, items):
        """Report the indexes in `items` now, and hand back everything else.

        **They cannot wait for the text.** Reading continuously, NVDA asks for
        the next line from `lineReached`, and `lineReached` is this
        notification -- so holding an index while holding its sentence means
        the next sentence never arrives and the reader simply stops. `_flush`
        already reports indexes ahead of their audio for the same reason; this
        moves them earlier still, by however long the sentence is held.

        The cost is the one this feature has: the cursor leads what is being
        spoken by up to one extra sentence.
        """
        rest = []
        for kind, value in items:
            if kind == "index":
                self._audioQueue.put(("index", value, None))
            else:
                rest.append((kind, value))
        return rest

    def _modeAfter(self, text):
        """-> the input mode in force at the end of `text`, or None.

        The same scan `_render` runs, asked earlier: entering with the mode
        the previous utterance left open, the last switch in `text` decides,
        and a piece with no switch stays in the carried mode.  Mirrors
        `_render`'s gates exactly -- with commands off nothing switches, and
        a generation whose input modes are stripped never carries one.
        """
        #: getattr because the setting only exists once NVDA has loaded the
        #: config for this synth; before that, commands are off.
        if (not getattr(self, "_acceptCommands", False)
                or self.INPUT_MODES_WORK is False):
            return None
        mode = self._inputMode
        for m in INPUT_MODE_CAPTURE_RE.finditer(text):
            mode = None if m.group(1).upper() == "TEXT" else m.group(1).upper()
        return mode

    def _join(self, item, epoch):
        """Group finished sentences so the engine has a boundary to breathe at.

        Alex breathes at a sentence boundary **inside** one utterance and
        nowhere else: measured, 1 sentence gives 0 breaths, 2 give 1, 3 give 2,
        6 give 5.  Reading continuously NVDA hands over one finished sentence
        at a time -- `speechWithoutPauses` flushes at the last full stop it can
        find -- so there is never a boundary inside what we are given, and
        nothing ever breathes.  Holding one sentence until the next arrives is
        the whole fix; the engine does the rest by itself.

        Bounded three ways, because an unbounded hold is a synthesizer that
        stops talking: two sentences is enough, `JOIN_MAX_CHARS` covers text
        with no full stop in it, and `JOIN_WAIT` covers the end of a document,
        where no next line is coming at all.  NVDA never tells a driver that an
        utterance was the last one -- it splits sequences at
        `EndUtteranceCommand` before we see them -- so the timeout is not a
        safety net, it is the mechanism for the final sentence.
        """
        #: Breathing is a comfort setting; keeping a song in one utterance is
        #: correctness (panthera-speech#11).  Turning the joiner off must not
        #: bring the verse-final pitch falls back, so a chunk in or entering
        #: a carried mode joins regardless of the setting.
        if not self._joinSentences and self._modeAfter(
                _joinFragments([v for k, v in item if k == "text"])) is None:
            return item
        #: Only while reading continuously.  NVDA marks those lines with an
        #: index; nothing else it sends this driver carries one, so an index is
        #: both the signal that more text is coming and the thing that asks for
        #: it.  Without this, arrowing through a list would be held too.
        if not any(kind == "index" for kind, _ in item):
            return item
        #: A break, a pitch change or a rate change divides the utterance where
        #: it stands, so joining across one would promise a boundary that is
        #: not there -- or, for a rate change, would speak the next utterance at
        #: the speed this one asked for.
        if any(kind in ("break", "pitch", "rate", "volume")
               for kind, _ in item):
            return item

        items = self._reportIndexes(item)
        text = _joinFragments([v for k, v in items if k == "text"])
        #: Mid-song, the prose bounds are the bug: a tune's prosodic "." and
        #: "!" phonemes count as sentence ends, so the two-sentence rule cut
        #: a song into verse-sized utterances and every verse-final note took
        #: the engine's utterance-final pitch fall (panthera-speech#11).
        #: While a mode is carried -- entering, or switched on by this very
        #: chunk -- the joiner holds the whole song, bounded only by
        #: TUNE_JOIN_MAX_CHARS, and an `[[inpt TEXT]]` in a joined chunk
        #: hands the loop back to the prose rules.
        carried = self._modeAfter(text) is not None
        while (not self._stopped and self._epoch == epoch
               and (len(text) < TUNE_JOIN_MAX_CHARS if carried else
                    (_sentenceEnds(text) < 2 and len(text) < JOIN_MAX_CHARS))):
            #: Two conditions before this is allowed to *block*, and between
            #: them they leave every latency that matters exactly as it was:
            #:
            #: - not the first utterance after a cancel, so starting to read is
            #:   as immediate as it ever was;
            #: - and there is a finished sentence in hand, and enough of
            #:   it.  NVDA hands over text that ends at a full stop, so
            #:   something arriving without one is not a line of a document --
            #:   it is an announcement that happens to carry an index, and
            #:   holding it is heard as the synthesizer lagging.  A *short*
            #:   thing carrying a full stop is an announcement too, which is
            #:   the half of this that had to be reported by ear.
            #:
            #: Either way it still absorbs whatever is *already* queued, which
            #: while reading continuously is usually the next line in any case.
            #: Three conditions, and the third was learned the expensive
            #: way -- see JOIN_MIN_CHARS.  A short announcement carrying a
            #: full stop is still an announcement.
            #:
            #: Mid-song, having a verse in hand is what earns the wait: note
            #: syntax carries no sentence end and no minimum length, and the
            #: indexes have already been reported, which is what asks NVDA
            #: for the next verse.
            block = (self._spokeSinceCancel
                     and (carried or (_sentenceEnds(text) >= 1
                                      and len(text) >= JOIN_MIN_CHARS)))
            try:
                nxt = self._queue.get(timeout=JOIN_WAIT if block else 0)
            except queue.Empty:
                break
            if nxt is None:
                #: terminate().  Put it back for the loop that owns it.
                self._queue.put(None)
                break
            if self._stopped or self._epoch != epoch:
                #: A cancel arrived during the wait, so `nxt` was spoken
                #: *after* it and belongs to the run that follows: absorbing
                #: it into this stale item is how the first utterance after
                #: a mid-song cancel used to vanish.  Hand it back for the
                #: fresh epoch; after a cancel the queue is empty, so the
                #: re-queue cannot reorder anything.
                self._queue.put(nxt)
                break
            if any(kind in ("break", "pitch", "rate", "volume")
                   for kind, _ in nxt):
                items.extend(nxt)
                break
            items.extend(self._reportIndexes(nxt))
            text = _joinFragments([v for k, v in items if k == "text"])
            carried = self._modeAfter(text) is not None
        return items

    def _flush(self, run, wpm, voice, adj, epoch, pending=None, vol=0):
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
        *start* of a line, and it is also what asks for the next line, so
        reporting it early keeps the pipeline fed rather than starving it.
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
            log.debug("%s: " % self.name + "speaking %r" % (text,))
        # Indexes go in before the audio rather than after rendering it.  They
        # belonged at the head of this utterance already -- see the docstring
        # above -- and now that the audio arrives in pieces there is no later
        # moment that would still be the head.
        if pending:
            for index in pending:
                self._audioQueue.put(("index", index, None))
            del pending[:]
        fed = []
        # What the user actually waits, measured where they wait it.  The two
        # numbers are different questions: how long until the first sound, and
        # how long the whole utterance took.  Streaming separated them --
        # before it, they were the same number.
        started = time.perf_counter()
        firstAt = []

        def sink(chunk):
            if self._stopped or self._epoch != epoch:
                return False        # interrupted: stop feeding, keep reading
            if not firstAt:
                firstAt.append(time.perf_counter())
            fed.append(len(chunk))
            self._audioQueue.put(("audio", chunk, epoch))
            return True

        # Long text goes to the engine a sentence at a time -- but only when
        # the host cannot stream.
        #
        # Splitting was measured against a host answering in one chunk, where
        # the wait before the first sound is set by how much audio has to exist
        # before any of it may be heard: 1323, 1383 and 1369 ms on one 792
        # character post.  Rendering the first sentence alone costs a fraction
        # of that.  Against a *streaming* host it buys nothing at all, because
        # the first chunk is already on its way while the rest renders --
        # measured on this driver, same text: 33.4 ms split against 30.8 ms
        # whole.
        #
        # And it is not free.  A sentence end is exactly where Alex breathes:
        # N sentences in one utterance give N-1 breaths, at the boundaries and
        # nowhere else, so cutting there is cutting the breath out.  Measured
        # on a four-sentence paragraph, whole against split into three: 9
        # pauses of 70 ms or more against 5, and 1.36 s of silence against
        # 1.08.  That is the same trade the streaming plan turned down, and it
        # is the opposite of what `joinSentences` above works to produce.
        #
        # So: stream and stay whole, or fall back and split.  In the fallback
        # the breath is worth giving up, because the alternative is a second
        # and a third of silence before anything is said at all.
        pieces = _splitUtterance(text) if not self._streaming else [text]
        pcm = None
        for piece in pieces:
            if self._stopped or self._epoch != epoch:
                break
            # Per piece, not per utterance: a failure in the middle of a
            # long post must not cost the rest of it.
            heard = len(fed)
            pcm = self._render(piece, wpm, voice, self._pitchOffset(adj),
                               sink=sink, volume=vol)
            if (pcm is None and len(fed) == heard and not self._stopped
                    and self._epoch == epoch):
                # Nothing was said and nothing was heard, and this utterance is
                # still the one the user is waiting for.  Two failures arrive
                # here.
                #
                # The host refused to stream, and streaming has just been
                # turned off -- so say it the old way rather than lose it,
                # because it could be the one telling the user what happened.
                #
                # Or a cancel retired the host a moment after this utterance
                # had started on it.  That cancel was for the utterance
                # *before* this one -- the queue was drained before this item
                # was taken off it -- so this text is still wanted, and the
                # retirement has left a fresh host to say it on.  Rule 3 at the
                # top of this file is the whole reason the case is handled
                # rather than reasoned about: an utterance dropped in silence
                # is the failure that matters.
                #
                # The epoch guard is what keeps it from costing anything.  Text
                # that really was cancelled is not rendered a second time only
                # to be thrown away, which would put back the wait the
                # retirement exists to remove.
                pcm = self._render(piece, wpm, voice,
                                   self._pitchOffset(adj), sink=sink,
                                   volume=vol)
        # Timing, at DEBUG, because "it lags on long text" is the report this
        # add-on gets most and it was never possible to check from a log.  Both
        # numbers, per utterance: a first sound that arrives late is a
        # different fault from an utterance that takes a long time in total,
        # and with Alex the second is expected -- he renders far more audio per
        # character than anything else here.
        if fed and log.isEnabledFor(log.DEBUG):
            done = time.perf_counter()
            frames = sum(fed) / 2.0
            log.debug("%s: " % self.name + "%d chars in %d piece(s) -> %.2f s of "
                      "audio in %d chunk(s); first sound after %.0f ms, "
                      "all of it by %.0f ms%s"
                      % (len(text), len(pieces), frames / OUT_RATE,
                         len(fed),
                         (firstAt[0] - started) * 1000.0,
                         (done - started) * 1000.0,
                         "" if self._epoch == epoch
                         else " (interrupted; the host was retired)"))
        if pcm is not None and fed and self._epoch == epoch:
            gap = self.PAUSE_MS.get(self._pauseMode, 0)
            if gap:
                self._audioQueue.put(("audio", _silence(gap), epoch))

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
            # Audio carries the epoch it was rendered under, checked *here*,
            # after it comes off the queue -- the only place a cancel cannot
            # slip past.  cancel() bumps the epoch and then drains this queue;
            # the worker checks the epoch and then puts.  A chunk landing
            # between those two steps survived the drain and played against
            # whatever the user asked for next, heard as a sentence from one
            # message bleeding into the one below it.  With a whole utterance
            # per put that was one narrow window; streaming made it twenty to
            # seventy of them.
            #
            # "index" and "done" are never tagged, so NVDA is always told the
            # utterance finished and always asks for the next one.
            if tag is not None and tag != self._epoch:
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
                    #
                    # A slice at a time, because the host hands over a whole
                    # utterance in one chunk and one `feed()` of that size
                    # blocks for seconds -- see FEED_SLICE.  The epoch is
                    # rechecked between slices, so an interrupt stops this
                    # utterance here rather than after the device has drained
                    # what it was already given.
                    for piece in _sliceAudio(value, FEED_SLICE):
                        if self._stopped or (tag is not None
                                             and tag != self._epoch):
                            break
                        now = time.perf_counter()
                        if self._fedUntil < now:
                            self._fedUntil = now
                        while (self._fedUntil - now > FEED_LEAD
                               and not self._stopped
                               and tag == self._epoch):
                            time.sleep(0.01)
                            now = time.perf_counter()
                        if tag is not None and tag != self._epoch:
                            break       # interrupted while we waited
                        self._fedUntil = max(self._fedUntil, now) +                             len(piece) / 2.0 / OUT_RATE
                        # Serialised against cancel()'s stop().
                        #
                        # NVDA's WASAPI player changes its stream state in
                        # both feed() and stop() without synchronising the
                        # two, so a stop landing while a feed is starting the
                        # stream leaves the next start to stall -- measured at
                        # 1839 ms in one session, which is the "two seconds
                        # and you hear nothing" people reported -- and can let
                        # frames from the abandoned utterance through into the
                        # stream that follows.
                        #
                        # `cancel()` waits only 20 ms for this lock, because
                        # it runs on NVDA's main thread.  That is enough only
                        # while what is held under it is short, which is the
                        # whole reason the audio is sliced above.
                        with self._playerLock:
                            if self._playerIdle:
                                self._playerIdle = False
                                t0 = time.perf_counter()
                                self._player.feed(piece)
                                ms = (time.perf_counter() - t0) * 1000.0
                                if ms >= 20.0 and log.isEnabledFor(log.DEBUG):
                                    log.debug(
                                        "%s: the audio device took " % self.name +
                                        "%.0f ms to start playing (%.0f ms of "
                                        "audio, after %s)"
                                        % (ms,
                                           len(piece) / 2.0 / OUT_RATE * 1000.0,
                                           "an interruption"
                                           if self._afterCancel
                                           else "the previous utterance ended"))
                                self._afterCancel = False
                            else:
                                self._player.feed(piece)
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
                log.debugWarning("%s: " % self.name + "feeding audio: %s" % e)

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
