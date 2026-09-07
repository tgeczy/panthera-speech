package com.pantheraspeech.tts

import java.util.Locale

/** Lexical half of the NVDA driver's pantheraabbrev.py rules. Dictionary
 * patterns are controlled separately in the native worker, on every request. */
internal object SpeechTextOptions {
    private val commands = Regex("\\[\\s*\\[[^\\]]{0,64}\\]\\s*\\]")
    private val inputMode = Regex("\\[\\[\\s*inpt\\s+[A-Za-z]{0,16}\\s*\\]\\]", RegexOption.IGNORE_CASE)
    private val acronyms = Regex("\\b(DR|ST|MR|MRS|JR|SR|FT|RD|CT|VS|ETC)\\b")
    private val titles = Regex("\\b(Prof|Capt|Blvd|Mrs|Gen|Sen|Rep|Gov|Ave|Dr|St|Mr|Ms|Lt|Jr|Sr|Rd|Ft|Ct)\\b")
    private val units = Regex("\\b(\\d+)\\s?(mm|cm|km|kg|g|m)\\b")
    private val roman = Regex("\\b(?=[MDCLXVI]{2,}\\b)(M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))\\b")
    private val doctor = Regex("\\bDr\\.(\\s+)(?=[A-Z][a-z])")
    private val possessiveX = Regex("\\bX(['’]s)\\b")

    private fun letters(text: String) = text.uppercase(Locale.ROOT).toList().joinToString(" ")

    fun abbreviations(text: String, expand: Boolean): String {
        var out = possessiveX.replace(text) { "ex${it.groupValues[1]}" }
        if (expand) return doctor.replace(out) { "Doctor${it.groupValues[1]}" }
        out = acronyms.replace(out) { letters(it.value) }
        out = titles.replace(out) { letters(it.value) }
        out = units.replace(out) { "${it.groupValues[1]} ${letters(it.groupValues[2])}" }
        return roman.replace(out) { if (it.value == "MIX") it.value else letters(it.value) }
    }

    fun prepare(text: String, acceptCommands: Boolean, expand: Boolean, generation: String): String = buildString {
        var at = 0
        for (match in commands.findAll(text)) {
            append(Emoji.describe(abbreviations(text.substring(at, match.range.first), expand), true))
            if (acceptCommands) {
                // Normalize delimiter spacing accepted by the engine. Never
                // rewrite numbers, abbreviations or emoji inside a command.
                val secondBracket = match.value.indexOf('[', 1)
                val command = "[[" + match.value.substring(secondBracket + 1).substringBefore(']') + "]]"
                // Lion's input modes have the same exclusion as the NVDA driver.
                if (generation != "lion" || !inputMode.matches(command)) append(command)
            }
            at = match.range.last + 1
        }
        append(Emoji.describe(abbreviations(text.substring(at), expand), true))
    }

}
