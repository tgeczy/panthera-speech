// Text on its way to the engine: how an utterance is cut into pieces.
//
// The engine cannot be interrupted. Measured on a Pixel Watch 2, a 700-character
// request rendered all 7041 slices of itself -- the whole fifty seconds of audio
// -- inside a single _SEStopSpeechAt call that returned noErr and took twenty
// seconds. Neither whereToStop value helps, the Speech Manager's 'rset' answers
// paramErr, and failing the scheduled slice from the audio shim is ignored. The
// engine finishes what it was given, and the only thing left to control is how
// much that is.
//
// So an utterance is handed over a piece at a time, and cancelling costs the
// remainder of the piece in flight rather than the remainder of the paragraph.
// Emulated Fred renders at about 2.5x realtime on that watch, which is what
// makes this affordable in the other direction too: while one piece plays, the
// next renders roughly two and a half times faster than it will be heard, so
// the seam between them is covered.
//
// The rules for *where* to cut are the desktop driver's, ported from
// synthDrivers/_panthera/text.py rather than reinvented, because they were
// arrived at by ear over several releases and the failure they prevent -- a
// full stop heard in the middle of a sentence -- is the one that matters.
package com.pantheraspeech.tts

object PantheraText {

    // Below this, an utterance is never cut. A short one costs nothing to
    // cancel, and leaving it whole keeps it byte-identical to what the desktop
    // WAV oracle renders for the same text.
    private const val SPLIT_MIN = 60

    // The first piece is cut as early as a boundary allows: it is the only one
    // the user waits for.
    private const val SPLIT_FIRST = 12

    // Every later piece is rendered while the one before it plays, so they are
    // cut long -- but not desktop-long. 160 characters there is a latency
    // tuning; here it is also the worst case for a cancellation, so it is sized
    // against the emulator: ~120 characters is around 7 s of speech, which is
    // under 3 s of render at 2.5x, and that is the delay an interrupted
    // TalkBack user actually feels.
    private const val SPLIT_TARGET = 120

    // How far past the target a sentence end is still worth waiting for, rather
    // than settling for a mid-sentence phrase break.
    private const val SPLIT_SLACK = 200

    private const val CLOSERS = ")]}\"”’'»"

    private val SENTENCE_END = Regex("[.!?][" + Regex.escape(CLOSERS) + "]*\\s+")
    private val PHRASE_END = Regex("[.!?,;:—–][ " + Regex.escape(CLOSERS) + "]*\\s+")
    private val WORD_BEFORE = Regex("[\\w']+$")

    // Words that end in a full stop without ending a sentence.
    private val ABBREVIATIONS = (
        "mr mrs ms dr prof rev hon sr jr st mt gen col sgt lt capt " +
        "ave rd blvd dept est fig vol no nos pp al vs etc approx " +
        "inc ltd co corp univ " +
        "jan feb mar apr jun jul aug sep sept oct nov dec " +
        "mon tue tues wed thu thur thurs fri sat sun am pm"
        ).split(" ").toSet()

    /** Offsets where a new sentence demonstrably begins.
     *
     * Conservative on purpose. A boundary that is not really one is heard as a
     * full stop in the middle of a sentence; everything doubtful is left alone,
     * which costs latency on that utterance and never costs a wrong reading. */
    private fun sentenceStarts(text: String): List<Int> {
        val out = ArrayList<Int>()
        for (m in SENTENCE_END.findAll(text)) {
            val start = m.range.last + 1
            if (start >= text.length) break
            if (text[m.range.first] == '.') {
                val w = WORD_BEFORE.find(text.substring(0, m.range.first))?.value
                // A single letter before a full stop is an initial or part of
                // an abbreviation, never the end of a sentence: "J. Smith",
                // "U.S. Army", "e.g. this one".
                if (w != null && (w.length == 1 || w.lowercase() in ABBREVIATIONS)) continue
            }
            val nxt = text[start]
            // What follows has to be able to open a sentence. A lower case
            // letter after a full stop is an abbreviation this list does not
            // know about. isLowerCase rather than isUpperCase, so that Arabic,
            // Hebrew and CJK -- which are neither -- can still be split.
            if (nxt.isLowerCase() || !(nxt.isLetterOrDigit() || nxt in "\"'“‘([")) continue
            out.add(start)
        }
        return out
    }

    /** Offsets where a new phrase begins: sentence ends, and the marks the
     * engine already breaks at. The sentence rules still apply to a full stop,
     * so an abbreviation is no more a phrase boundary here than a sentence one. */
    private fun phraseStarts(text: String): List<Int> {
        val sentences = sentenceStarts(text).toSet()
        val out = ArrayList<Int>()
        for (m in PHRASE_END.findAll(text)) {
            val start = m.range.last + 1
            if (start >= text.length) break
            if (text[m.range.first] in ".!?") {
                if (start in sentences) out.add(start)
                continue
            }
            out.add(start)
        }
        return out
    }

    /** `text` in pieces that rejoin to exactly `text`.
     *
     * Never fewer characters than went in, and never a cut anywhere except a
     * boundary the text already had -- so an utterance with none comes back
     * whole and is rendered exactly as it was before. */
    fun split(text: String): List<String> {
        if (text.length <= SPLIT_MIN) return listOf(text)
        val sentences = sentenceStarts(text)
        val phrases = phraseStarts(text)

        fun firstPast(offsets: List<Int>, lower: Int, upper: Int?): Int? =
            offsets.firstOrNull { it >= lower && (upper == null || it <= upper) }

        val pieces = ArrayList<String>()
        var at = 0
        var want = SPLIT_FIRST
        while (true) {
            // A sentence end if there is one within reach, a phrase boundary
            // only if there is not.
            val cut = firstPast(sentences, at + want, at + want + SPLIT_SLACK)
                ?: firstPast(phrases, at + want, null)
            if (cut == null || cut <= at) break
            pieces.add(text.substring(at, cut))
            at = cut
            want = SPLIT_TARGET
        }
        pieces.add(text.substring(at))
        return pieces.filter { it.isNotBlank() }
    }
}
