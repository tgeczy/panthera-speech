// Text as the engine actually reads it: MacRoman, not UTF-8.
//
// Sent as UTF-8, one em dash arrives as three bytes and is read a character at
// a time -- "he paused - then left" came out of the desktop driver as "he
// paused, AI then left", which is how a tester found this. MacRoman puts the em
// dash at 0xD1, the curly quotes at 0xD2..0xD5 and the ellipsis at 0xC9, so
// encoding properly is the whole fix, and there is no table of symbol names to
// maintain.
//
// Android has no MacRoman charset, so the high half is a table here rather than
// a Charset lookup. It is generated from Python's own `mac_roman` codec, which
// is where the desktop driver gets it, so the two cannot drift.
//
// The fold table and the unmappable rule are ports of
// synthDrivers/_panthera/text.py. Both were arrived at by ear on the desktop,
// and every entry in them is a bug somebody reported.
package com.pantheraspeech.tts

import java.io.ByteArrayOutputStream
import java.text.Normalizer

internal object MacRoman {

    /** Unicode -> MacRoman for the high half; 0x00..0x7F is ASCII, unchanged. */
    private val HIGH: Map<Int, Byte> = mapOf(
        0x00C4 to 0x80.toByte(), 0x00C5 to 0x81.toByte(), 0x00C7 to 0x82.toByte(), 0x00C9 to 0x83.toByte(),
        0x00D1 to 0x84.toByte(), 0x00D6 to 0x85.toByte(), 0x00DC to 0x86.toByte(), 0x00E1 to 0x87.toByte(),
        0x00E0 to 0x88.toByte(), 0x00E2 to 0x89.toByte(), 0x00E4 to 0x8A.toByte(), 0x00E3 to 0x8B.toByte(),
        0x00E5 to 0x8C.toByte(), 0x00E7 to 0x8D.toByte(), 0x00E9 to 0x8E.toByte(), 0x00E8 to 0x8F.toByte(),
        0x00EA to 0x90.toByte(), 0x00EB to 0x91.toByte(), 0x00ED to 0x92.toByte(), 0x00EC to 0x93.toByte(),
        0x00EE to 0x94.toByte(), 0x00EF to 0x95.toByte(), 0x00F1 to 0x96.toByte(), 0x00F3 to 0x97.toByte(),
        0x00F2 to 0x98.toByte(), 0x00F4 to 0x99.toByte(), 0x00F6 to 0x9A.toByte(), 0x00F5 to 0x9B.toByte(),
        0x00FA to 0x9C.toByte(), 0x00F9 to 0x9D.toByte(), 0x00FB to 0x9E.toByte(), 0x00FC to 0x9F.toByte(),
        0x2020 to 0xA0.toByte(), 0x00B0 to 0xA1.toByte(), 0x00A2 to 0xA2.toByte(), 0x00A3 to 0xA3.toByte(),
        0x00A7 to 0xA4.toByte(), 0x2022 to 0xA5.toByte(), 0x00B6 to 0xA6.toByte(), 0x00DF to 0xA7.toByte(),
        0x00AE to 0xA8.toByte(), 0x00A9 to 0xA9.toByte(), 0x2122 to 0xAA.toByte(), 0x00B4 to 0xAB.toByte(),
        0x00A8 to 0xAC.toByte(), 0x2260 to 0xAD.toByte(), 0x00C6 to 0xAE.toByte(), 0x00D8 to 0xAF.toByte(),
        0x221E to 0xB0.toByte(), 0x00B1 to 0xB1.toByte(), 0x2264 to 0xB2.toByte(), 0x2265 to 0xB3.toByte(),
        0x00A5 to 0xB4.toByte(), 0x00B5 to 0xB5.toByte(), 0x2202 to 0xB6.toByte(), 0x2211 to 0xB7.toByte(),
        0x220F to 0xB8.toByte(), 0x03C0 to 0xB9.toByte(), 0x222B to 0xBA.toByte(), 0x00AA to 0xBB.toByte(),
        0x00BA to 0xBC.toByte(), 0x03A9 to 0xBD.toByte(), 0x00E6 to 0xBE.toByte(), 0x00F8 to 0xBF.toByte(),
        0x00BF to 0xC0.toByte(), 0x00A1 to 0xC1.toByte(), 0x00AC to 0xC2.toByte(), 0x221A to 0xC3.toByte(),
        0x0192 to 0xC4.toByte(), 0x2248 to 0xC5.toByte(), 0x2206 to 0xC6.toByte(), 0x00AB to 0xC7.toByte(),
        0x00BB to 0xC8.toByte(), 0x2026 to 0xC9.toByte(), 0x00A0 to 0xCA.toByte(), 0x00C0 to 0xCB.toByte(),
        0x00C3 to 0xCC.toByte(), 0x00D5 to 0xCD.toByte(), 0x0152 to 0xCE.toByte(), 0x0153 to 0xCF.toByte(),
        0x2013 to 0xD0.toByte(), 0x2014 to 0xD1.toByte(), 0x201C to 0xD2.toByte(), 0x201D to 0xD3.toByte(),
        0x2018 to 0xD4.toByte(), 0x2019 to 0xD5.toByte(), 0x00F7 to 0xD6.toByte(), 0x25CA to 0xD7.toByte(),
        0x00FF to 0xD8.toByte(), 0x0178 to 0xD9.toByte(), 0x2044 to 0xDA.toByte(), 0x20AC to 0xDB.toByte(),
        0x2039 to 0xDC.toByte(), 0x203A to 0xDD.toByte(), 0xFB01 to 0xDE.toByte(), 0xFB02 to 0xDF.toByte(),
        0x2021 to 0xE0.toByte(), 0x00B7 to 0xE1.toByte(), 0x201A to 0xE2.toByte(), 0x201E to 0xE3.toByte(),
        0x2030 to 0xE4.toByte(), 0x00C2 to 0xE5.toByte(), 0x00CA to 0xE6.toByte(), 0x00C1 to 0xE7.toByte(),
        0x00CB to 0xE8.toByte(), 0x00C8 to 0xE9.toByte(), 0x00CD to 0xEA.toByte(), 0x00CE to 0xEB.toByte(),
        0x00CF to 0xEC.toByte(), 0x00CC to 0xED.toByte(), 0x00D3 to 0xEE.toByte(), 0x00D4 to 0xEF.toByte(),
        0xF8FF to 0xF0.toByte(), 0x00D2 to 0xF1.toByte(), 0x00DA to 0xF2.toByte(), 0x00DB to 0xF3.toByte(),
        0x00D9 to 0xF4.toByte(), 0x0131 to 0xF5.toByte(), 0x02C6 to 0xF6.toByte(), 0x02DC to 0xF7.toByte(),
        0x00AF to 0xF8.toByte(), 0x02D8 to 0xF9.toByte(), 0x02D9 to 0xFA.toByte(), 0x02DA to 0xFB.toByte(),
        0x00B8 to 0xFC.toByte(), 0x02DD to 0xFD.toByte(), 0x02DB to 0xFE.toByte(), 0x02C7 to 0xFF.toByte(),
    )

    /** Characters MacRoman has no room for, mapped to something it can say.
     *
     * Everything typographic that matters -- em dash, en dash, curly quotes,
     * ellipsis -- MacRoman already has, so it is not listed here. */
    private val FOLD: Map<Int, String> = mapOf(
        0x00A0 to " ", 0x2007 to " ", 0x2009 to " ", 0x202F to " ",   // fixed spaces
        0x2011 to "-", 0x2012 to "-", 0x2015 to "-", 0x2212 to "-",   // more dashes
        0x2032 to "'", 0x2033 to "\"", 0x02BC to "'",                 // primes

        // The typographic apostrophe, and the reason a sentence full of them
        // fell apart. MacRoman *has* it, at 0xD5 -- but 0xD5 is the right
        // single QUOTATION mark, and the engine's front end treats it as one:
        // it breaks the phrase there. "Canopy's investments" came out as
        // "Canopy", 250 ms of silence, "s investments", and the sentence ran
        // 1.57 s longer for the pauses it grew. A straight apostrophe is an
        // apostrophe. Curly *double* quotes are left alone: those really are
        // quotation marks.
        0x2018 to "'", 0x2019 to "'",
        0x2044 to "/",                                                // fraction slash

        // Hungarian's two long vowels, which no generation has ever spoken.
        // MacRoman has every accent Western European typography needed in 1984
        // and the double acute is not among them, so these fell through to the
        // unmappable rule and came out as a gap -- in Tiger, Leopard, Snow
        // Leopard and Lion alike. Reported as "most of them are spoken", which
        // is exactly what six-of-eight sounds like. Folded to the diaeresis
        // rather than to bare o and u: in Hungarian these are the long
        // counterparts of o-umlaut and u-umlaut, the same vowel held longer, so
        // the diaeresis is the nearest thing MacRoman has, and it is near.
        0x0151 to "ö", 0x0150 to "Ö",                       // o-double-acute
        0x0171 to "ü", 0x0170 to "Ü",                       // u-double-acute

        // A stroke is not a combining mark, so these four survive the
        // decomposition below and would still arrive as gaps: L-stroke is one
        // indivisible character to Unicode rather than L plus a mark, so there
        // is nothing to strip. Listed because "Lodz" reading as "odz" is the
        // same complaint in Polish, and it is two lines to not have it.
        0x0141 to "L", 0x0142 to "l",
        0x0110 to "D", 0x0111 to "d",
    )

    /** One code point's byte, or null if MacRoman cannot spell it at all. */
    private fun direct(cp: Int): Byte? =
        if (cp < 0x80) cp.toByte() else HIGH[cp]

    /** Anything MacRoman cannot spell loses its accent, or becomes a space.
     *
     * Strip the diacritic before giving up. MacRoman covers Western Europe as
     * of 1984 and no further, so every Polish, Czech, Turkish and Romanian
     * letter it never heard of used to arrive as a gap: "Lodz" was read as
     * "odz", with a hole where the L should be. Decomposing and keeping the
     * base letter gives "Lodz" -- wrong the way an English-speaking reader is
     * wrong, rather than absent.
     *
     * A character with no decomposition at all becomes a space, and the
     * alternative is worse than it looks: a replacement character gives "?",
     * which the engine reads as a *question* and lifts the intonation of the
     * whole sentence for. A gap is closer to the truth than a wrong inflection,
     * and it leaves a real "?" the user typed meaning what it says. */
    private fun fallback(cp: Int, out: ByteArrayOutputStream) {
        val bare = Normalizer.normalize(String(Character.toChars(cp)), Normalizer.Form.NFD)
            .filter { Character.getType(it) != Character.NON_SPACING_MARK.toInt() }
        var wrote = false
        for (ch in bare) {
            val b = direct(ch.code) ?: continue
            out.write(b.toInt()); wrote = true
        }
        if (!wrote) out.write(' '.code)
    }

    /** -> the engine's bytes. */
    fun encode(text: String): ByteArray {
        val out = ByteArrayOutputStream(text.length + 16)
        var i = 0
        while (i < text.length) {
            val cp = text.codePointAt(i)
            i += Character.charCount(cp)
            val folded = FOLD[cp]
            if (folded != null) {
                for (ch in folded) out.write((direct(ch.code) ?: ' '.code.toByte()).toInt())
                continue
            }
            val b = direct(cp)
            if (b != null) out.write(b.toInt()) else fallback(cp, out)
        }
        return out.toByteArray()
    }
}
