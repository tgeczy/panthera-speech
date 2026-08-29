# -*- coding: utf-8 -*-
"""The worker that renders, the joiner that decides what an utterance is, and
the feeder that pays out audio to the player.

Split out of `pantheradriver.py` unchanged.  Three threads' worth of rules,
and nearly every one of them was learned by breaking it:

* the render loop is never blocked on playback, because `feed()` blocks for as
  long as the audio lasts and NVDA paces what it sends on `synthDoneSpeaking`;
* rendered audio never waits in a holding area to be discarded -- 367 of 435
  utterances thrown away in one measured session, heard as words cut in half;
* work is discarded by draining, never by stamping, because a generation
  counter compared at render time once froze a driver silent for 194
  consecutive utterances, and permanently silent is far worse than
  occasionally stale.

The joiner is here rather than with the text helpers on purpose.  Where a
sentence *ends* is a question about a string and lives in `text`; whether to
hold one and wait for the next is a question about time, a queue and a worker,
and it can only be answered here.

A plain mixin: nothing in it is a settings accessor, so NVDA's metaclass has
no opinion about where it lives.
"""
import queue
import time

from logHandler import log
from synthDriverHandler import synthDoneSpeaking, synthIndexReached

from .audio import SENTENCE_PAUSE_FACTOR, _silence, _sliceAudio
from .constants import FEED_LEAD, FEED_SLICE, OUT_RATE
from .text import (INPUT_MODE_CAPTURE_RE, SPLIT_MIN, _joinFragments,
                   _sentenceEnds, _splitUtterance)

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


class SpeechPipelineMixin(object):
    """Rendering, joining and feeding, on their own threads."""

    #: When the last interruption happened, for the one measurement a listener
    #: can actually confirm by ear.  A class attribute so no driver `__init__`
    #: needs to know this mixin keeps state.
    _cancelledAt = 0.0

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
                                if (self._afterCancel
                                        and log.isEnabledFor(log.DEBUG)):
                                    #: The number a listener actually feels:
                                    #: their key, and the first sound after
                                    #: it.  Every stage of an interruption is
                                    #: already logged and every stage measures
                                    #: fast, so this exists because the pieces
                                    #: summing to less than the whole is worth
                                    #: being able to see.
                                    #:
                                    #: **Debug only, deliberately.**  It was
                                    #: written to warn above 400 ms, while a
                                    #: stall was being hunted.  Nobody is
                                    #: hunting it now, and a warning that
                                    #: fires on a busy machine is the mistake
                                    #: this driver already made once: one real
                                    #: log was forty host startups in ninety
                                    #: seconds at warning level, burying the
                                    #: single line that mattered.  A
                                    #: measurement kept for a question nobody
                                    #: is asking belongs where it costs
                                    #: nothing to ignore.
                                    log.debug(
                                        "%s: " % self.name +
                                        "interrupted; next sound %.0f ms later"
                                        % ((time.perf_counter()
                                            - self._cancelledAt) * 1000.0))
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
