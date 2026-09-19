# Speech markers and continuous prosody

Development status, September 19, 2026. The released driver still uses the
3.1.0 behavior. The working tree includes the marker protocol and driver
integration described below; neither has shipped yet.

Issue #21 concerns Earcons and Speech Rules, formerly Phonetic Punctuation.
Its synchronous sound chain is a `BaseCallbackCommand` followed by a
`BreakCommand` lasting the chain's duration. NVDA 2026.2 converts the
callback to an index. Sound-only sequences deliberately omit the break.

In 3.1.0 with joining disabled, Panthera reports known boundary indexes at
playback. However, a break flushes the preceding text as a complete engine
utterance, applying ending prosody inside the listener's sentence. An
interior index without a break does not flush, but its position is unknown.

## Native evidence

Public sync callbacks arrive according to task scheduling and our accelerated
clock. Reading the collected PCM count there gives inconsistent positions.
Lion's private notification frame counter also wraps and changes its time
base across sentences.

A research adapter instead intercepts named C++ virtual methods:

- `MTBEAudioUnitSoundOutput::QueueSamples` counts produced samples.
- `MTBEAudioUnitSoundOutput::Wakeup` identifies its temporary one-frame
  silent startup sample, excluded from that count.
- `MT3BNotifier::WantSync` enables sync processing; `NotifySync` records
  the count synchronously, before asynchronous callback scheduling.

Alex uses the derived `MTPBNotifier` table. Intercepting only the base table
misses Alex. The adapter matches named methods in named tables; it modifies
no engine files or machine instructions and reads no private field offsets.
These remain private C++ interfaces requiring validation and a fallback.

The callback-free adapter passed 28 native i386 cases at 180 wpm: Tiger
Fred, and Fred/Alex on Leopard, Snow Leopard and Lion, each over a short
phrase, two sentences, paragraphs, and an embedded pause. In every case,
the captured sample stream, excluding only Wakeup's startup samples,
equaled the final host PCM exactly. Interception preserved the control
audio, and both interior syncs arrived in order at the reconstructed sample
positions. Twenty reused Lion Alex renders at 387 wpm also produced
identical PCM and marker positions. These are native Windows measurements
using Media Foundation, not cancellation or emulation tests.

Use hexadecimal sync IDs: Tiger reads decimal arguments as Fixed, unlike
newer generations.

## Preserve the sentence, insert the pause during playback

`[[sync A]][[slnc 250]][[sync B]]` does not locate both pause edges: all
seven tested voice/generation combinations reported both syncs after the
embedded silence. Instead, render the whole sentence with interior syncs
and insert the requested pause into playback at its marker. Report the
index before the pause so the sound can play in the space reserved for it.

An offline Lion Alex prototype at 387 wpm preserves every speech sample
from a continuous reference while inserting a 250 ms sound. Its sentence
contains 35,892 speech frames; separately rendered fragments contain
37,266. Tomi confirmed the offline sample by ear: the sentence continues
naturally across the sound. This does not yet establish live NVDA callback
timing or sound-device synchronization.

A second offline test uses Tomi's two-sentence Run-dialog announcement.
At 387 wpm, adding an interior sync preserves all 119,114 reference frames
for Lion Alex and all 109,992 for Leopard Alex. The playback prototype
inserts its sound after "Internet resource," and retains the later
sentence-boundary audio unchanged. Tomi confirmed both Lion and Leopard
retain the engine-generated breath after "and Windows will open it for
you." This is the same location by ear, not identical audio across the
generations. Both sentences are
supplied in one engine request, so this does not resolve Say All lookahead.

Do not append a sync for every final index. In Tiger Fred and Lion Alex,
a sync after final punctuation was never reported. Lion Alex's
`One two three.[[sync 0x66]]` also changed the render from 10,205 to 15,667
frames at 387 wpm, including earlier speech samples. Leading, adjacent,
and between-sentence interior markers passed the tested controls. Keep
known head/tail indexes at their known playback boundaries.

## Working implementation (unreleased, September 19)

`src/tiger_host_markers.c` now contains the optional runtime adapter. It
validates the named method slots within their symbol/section bounds before
changing any table, including Alex's derived notifier table. The loader's
image pages are already writable for relocation. Originals use nested guest
calls under emulation. A locked producer frontier orders markers against
streamed PCM; each request resets the marker queue, and cancellation retires
its state before stopping the engine.

TGR5 uses the TGR4 request header with magic `0x54475235`. Responses start
with the existing TGRS/status header. Subsequent records are:

| First word | Following data |
| --- | --- |
| 1–1024 | that many signed 16-bit mono PCM frames |
| `0x80000001` | unsigned sync ID, unsigned absolute PCM frame offset |
| `0x80000002` | signed runtime error code |
| 0 | end of response |

A marker is emitted exactly at its PCM cursor, before any subsequent audio.
Offsets exclude playback pauses inserted by the client. Status `-32001`
refuses an unavailable adapter before speech; error record `-32002` reports
a violated timeline invariant. TGR3/TGR4 formats are unchanged, including
when used after a marker request in the same process.

The common NVDA driver uses this path with continuous-reading joining off,
embedded commands off, and ordinary text input. `marker_pipeline.py` keeps
text, indexes and breaks in one render until a real prosody change. It puts
indexes on playback callbacks and inserts the requested break at that PCM
position. Known head/tail indexes remain external. Text commands from the
document are stripped before adding owned syncs. A lexical rewrite crossing
an index (for example `Dr.` / `Smith`) falls back before rendering, as do
unavailable marker interfaces. Existing joining-on behavior stays intact.
Tiger's separate NVDA driver has not been migrated to this pipeline.

Tomi also confirmed by ear that **both new driver-pipeline recordings**
retain Alex's breath after "and Windows will open it for you." These are
the Lion and Leopard Run-dialog recordings with an earcon earlier in the
sentence. This confirmation covers the new pipeline recordings as well as
the earlier offline prototype; live NVDA/Earcons interaction remains to test.

Native Windows checks cover the new protocol, driver preprocessing and PCM
splicing. Lion, Leopard and Snow Leopard Alex retain exact continuous PCM
through the real driver. Lion's two integration cases also pass through the
32-bit DLL. Twenty interrupt/replacement cases (five per generation, Tiger
Fred and the three Alex banks) preserve replacement PCM and discard old IDs.
Android ARMv7/AArch64 syntax checks pass. Subsequent Windows Unicorn testing
passed Tiger Fred's four transport cases, but the Leopard Alex Run-dialog
case differed (109,992 versus 110,572 frames). The cause is unresolved.
**The release therefore refuses TGR5 under `TIGER_UC` before synthesis**;
clients fall back to the existing protocol. This also covers Box-backed
hosts, which share that seam. JNI clients do not request TGR5.

The strict Fred transport test exposed an existing Lion variation. Thirty
renders of `One  two three.` at 387 wpm on the released host produced 27
10,744-frame renders and three 10,746-frame renders. Candidate blocking and
marked requests produced the **same two PCM hashes**. Do not describe that
voice's repeated renders as universally byte-identical or loosen the strict
check to conceal it. The Alex controls above remain exact.

Reproduction: `tools/check_markers.py --host C:/path/to/tiger_host.exe
--tree C:/path/to/engine --voice Alex`. Native driver tests accept
`PANTHERA_MARKER_HOST` and `PANTHERA_MARKER_TREE`; a DLL requires a 32-bit
Python process. Neither command deploys the host or plays audio.

## Remaining release validation

Exercise the complete NVDA/Earcons interaction by ear, expand voice and
input-boundary coverage, and validate nested guest calls under emulation.
Nonzero sync timing arguments deliberately fail the new request; only
zero-offset sync commands have been validated.

Linux runtime validation is still needed. Existing SAPI clients continue
using the unchanged protocol; replacing their proportional bookmark
estimates is separate integration work. JNI/library clients use a separate
boundary and do not request these markers.

Tomi approved keeping the checkbox under its more precise label:
**Join text blocks during Say All to preserve breaths**. The `joinSentences`
configuration key, enabled default and joining behavior remain unchanged.
Enabling it can report reading-position indexes and callbacks early.
Disabling it preserves the tested breaths within supplied utterances; it
does not join separate blocks. NVDA 2026.2's `lineReached` both updates the
reading position and requests more text, and the joiner obtains that text
by reporting indexes early. Accurate markers do not supply future text.
Removing this setting would still require solving that text-supply problem.
