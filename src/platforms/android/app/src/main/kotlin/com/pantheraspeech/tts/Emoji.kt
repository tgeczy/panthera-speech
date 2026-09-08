// license:BSD-3-Clause
// copyright-holders:David Sexton
package com.pantheraspeech.tts

/**
 * Emoji-to-text translation, run before [MacRoman.encode] so a recognised
 * emoji speaks as its name ("grinning face") instead of vanishing into the
 * space that anything MacRoman cannot spell becomes. A 1984 Western European
 * encoding has no emoji and never will, so describing them is the only way
 * this engine can say one -- and TalkBack hands them over constantly.
 *
 * Ported from doubledroid, unchanged but for the package and these comments,
 * and kept under its own BSD-3-Clause licence and David Sexton's copyright.
 * The tables are generated from Unicode's own emoji-test.txt. Both the BSD
 * notice and Unicode's data licence accompany the Android distribution.
 *
 * Emoji sit outside the BMP, and flags, skin tones and ZWJ sequences span
 * several code points, so this walks Unicode code points rather than UTF-16
 * chars like the rest of the text handling does.
 *
 * [NAMES] and [FLAGS] are generated from Unicode's emoji-test.txt (v16.0,
 * the fully-qualified, single-base-code-point subset - i.e. every emoji this
 * function can actually recognise on its own, skin-tone modifiers aside).
 * Multi-person ZWJ sequences (e.g. a family or a couple holding hands) speak
 * as their individual member emoji in turn rather than one combined phrase:
 * what matters is that every character is heard as some word, not that
 * composite glyphs are narrated perfectly.
 */
internal object Emoji {

    private const val VARIATION_SELECTOR_15 = 0xFE0E
    private const val VARIATION_SELECTOR_16 = 0xFE0F
    private const val ZERO_WIDTH_JOINER = 0x200D
    private const val REGIONAL_INDICATOR_BASE = 0x1F1E6 // 'A'
    private val REGIONAL_INDICATORS = REGIONAL_INDICATOR_BASE..0x1F1FF // 'A'-'Z'

    /** Three or more of the exact same emoji back to back collapse to a
     * count instead of being spoken one at a time - see [describe]. */
    private const val REPEAT_THRESHOLD = 3

    /**
     * Replaces each recognised emoji code point with " <description> "
     * (space-padded so it word-breaks from surrounding text). A pair of
     * regional-indicator letters (a flag) is translated as a unit, falling
     * back to spelling out the two-letter code for a territory not in
     * [FLAGS]. A skin-tone modifier always immediately follows the emoji it
     * colors, so (when that emoji is one we described) it's folded into that
     * emoji's own unit - "raising hands" + light skin tone modifier becomes
     * "raising hands light skin tone" - rather than spoken on its own. The
     * emoji variation selector and zero-width joiners are dropped silently.
     * Anything unrecognised passes through unchanged for
     * the MacRoman encoder to handle.
     *
     * When [collapseRepeats] is set, a run of [REPEAT_THRESHOLD] or more
     * identical emoji units (same emoji, same skin tone if any, or the same
     * flag) speaks as "<count> <description>" instead of the description
     * repeated - e.g. "😂😂😂😂😂" becomes "5 face with tears of joy" rather
     * than five back-to-back "face with tears of joy"s. Runs shorter than
     * the threshold still speak individually, one description per emoji.
     */
    fun describe(text: String, collapseRepeats: Boolean): String {
        val codePoints = text.codePoints().toArray()
        val out = StringBuilder(text.length)
        var i = 0
        while (i < codePoints.size) {
            val unitLen = unitLength(codePoints, i)
            if (unitLen > 0) {
                val phrase = phraseFor(codePoints, i)
                val repeats = if (collapseRepeats) repeatCount(codePoints, i, unitLen) else 1
                if (repeats >= REPEAT_THRESHOLD) {
                    out.append(' ').append(repeats).append(' ').append(phrase).append(' ')
                } else {
                    repeat(repeats) { out.append(' ').append(phrase).append(' ') }
                }
                i += unitLen * repeats
                continue
            }
            val cp = codePoints[i]
            if (cp != VARIATION_SELECTOR_15 && cp != VARIATION_SELECTOR_16 &&
                cp != ZERO_WIDTH_JOINER && cp !in SKIN_TONES) {
                out.appendCodePoint(cp)
            }
            i++
        }
        return out.toString()
    }

    /** Code points consumed by the recognised emoji starting at [i] (a bare
     * name, a name plus its skin-tone modifier, or a flag pair), or 0 if
     * nothing recognised starts there. */
    private fun unitLength(codePoints: IntArray, i: Int): Int {
        val cp = codePoints[i]
        return when {
            cp in NAMES ->
                if (i + 1 < codePoints.size && codePoints[i + 1] in SKIN_TONES) 2 else 1
            cp in REGIONAL_INDICATORS && i + 1 < codePoints.size &&
                codePoints[i + 1] in REGIONAL_INDICATORS -> 2
            else -> 0
        }
    }

    /** The spoken phrase for the unit [unitLength] found starting at [i]. */
    private fun phraseFor(codePoints: IntArray, i: Int): String {
        val cp = codePoints[i]
        if (cp in REGIONAL_INDICATORS) {
            val code = "" + regionalLetter(cp) + regionalLetter(codePoints[i + 1])
            return FLAGS[code] ?: "$code flag"
        }
        val name = NAMES.getValue(cp)
        val skinTone = if (i + 1 < codePoints.size) SKIN_TONES[codePoints[i + 1]] else null
        return if (skinTone != null) "$name $skinTone" else name
    }

    /** How many times the [unitLen]-code-point unit at [start] repeats back
     * to back (1 if it doesn't repeat at all). */
    private fun repeatCount(codePoints: IntArray, start: Int, unitLen: Int): Int {
        var count = 1
        var pos = start + unitLen
        while (pos + unitLen <= codePoints.size) {
            for (k in 0 until unitLen) {
                if (codePoints[pos + k] != codePoints[start + k]) return count
            }
            count++
            pos += unitLen
        }
        return count
    }

    private fun regionalLetter(cp: Int): Char = 'A' + (cp - REGIONAL_INDICATOR_BASE)

    /** Fitzpatrick emoji modifiers (types 1-2 through 6), keyed by code point. */
    private val SKIN_TONES = mapOf(
        0x1F3FB to "light skin tone",
        0x1F3FC to "medium-light skin tone",
        0x1F3FD to "medium skin tone",
        0x1F3FE to "medium-dark skin tone",
        0x1F3FF to "dark skin tone",
    )

    // ISO 3166 two-letter region code -> spoken flag description, generated
    // from emoji-test.txt's country-flag subgroup.
    private val FLAGS = mapOf(
        "AC" to "Ascension Island flag",
        "AD" to "Andorra flag",
        "AE" to "United Arab Emirates flag",
        "AF" to "Afghanistan flag",
        "AG" to "Antigua and Barbuda flag",
        "AI" to "Anguilla flag",
        "AL" to "Albania flag",
        "AM" to "Armenia flag",
        "AO" to "Angola flag",
        "AQ" to "Antarctica flag",
        "AR" to "Argentina flag",
        "AS" to "American Samoa flag",
        "AT" to "Austria flag",
        "AU" to "Australia flag",
        "AW" to "Aruba flag",
        "AX" to "Aland Islands flag",
        "AZ" to "Azerbaijan flag",
        "BA" to "Bosnia and Herzegovina flag",
        "BB" to "Barbados flag",
        "BD" to "Bangladesh flag",
        "BE" to "Belgium flag",
        "BF" to "Burkina Faso flag",
        "BG" to "Bulgaria flag",
        "BH" to "Bahrain flag",
        "BI" to "Burundi flag",
        "BJ" to "Benin flag",
        "BL" to "St. Barthelemy flag",
        "BM" to "Bermuda flag",
        "BN" to "Brunei flag",
        "BO" to "Bolivia flag",
        "BQ" to "Caribbean Netherlands flag",
        "BR" to "Brazil flag",
        "BS" to "Bahamas flag",
        "BT" to "Bhutan flag",
        "BV" to "Bouvet Island flag",
        "BW" to "Botswana flag",
        "BY" to "Belarus flag",
        "BZ" to "Belize flag",
        "CA" to "Canada flag",
        "CC" to "Cocos (Keeling) Islands flag",
        "CD" to "Congo - Kinshasa flag",
        "CF" to "Central African Republic flag",
        "CG" to "Congo - Brazzaville flag",
        "CH" to "Switzerland flag",
        "CI" to "Cote d'Ivoire flag",
        "CK" to "Cook Islands flag",
        "CL" to "Chile flag",
        "CM" to "Cameroon flag",
        "CN" to "China flag",
        "CO" to "Colombia flag",
        "CP" to "Clipperton Island flag",
        "CQ" to "Sark flag",
        "CR" to "Costa Rica flag",
        "CU" to "Cuba flag",
        "CV" to "Cape Verde flag",
        "CW" to "Curacao flag",
        "CX" to "Christmas Island flag",
        "CY" to "Cyprus flag",
        "CZ" to "Czechia flag",
        "DE" to "Germany flag",
        "DG" to "Diego Garcia flag",
        "DJ" to "Djibouti flag",
        "DK" to "Denmark flag",
        "DM" to "Dominica flag",
        "DO" to "Dominican Republic flag",
        "DZ" to "Algeria flag",
        "EA" to "Ceuta and Melilla flag",
        "EC" to "Ecuador flag",
        "EE" to "Estonia flag",
        "EG" to "Egypt flag",
        "EH" to "Western Sahara flag",
        "ER" to "Eritrea flag",
        "ES" to "Spain flag",
        "ET" to "Ethiopia flag",
        "EU" to "European Union flag",
        "FI" to "Finland flag",
        "FJ" to "Fiji flag",
        "FK" to "Falkland Islands flag",
        "FM" to "Micronesia flag",
        "FO" to "Faroe Islands flag",
        "FR" to "France flag",
        "GA" to "Gabon flag",
        "GB" to "United Kingdom flag",
        "GD" to "Grenada flag",
        "GE" to "Georgia flag",
        "GF" to "French Guiana flag",
        "GG" to "Guernsey flag",
        "GH" to "Ghana flag",
        "GI" to "Gibraltar flag",
        "GL" to "Greenland flag",
        "GM" to "Gambia flag",
        "GN" to "Guinea flag",
        "GP" to "Guadeloupe flag",
        "GQ" to "Equatorial Guinea flag",
        "GR" to "Greece flag",
        "GS" to "South Georgia and South Sandwich Islands flag",
        "GT" to "Guatemala flag",
        "GU" to "Guam flag",
        "GW" to "Guinea-Bissau flag",
        "GY" to "Guyana flag",
        "HK" to "Hong Kong SAR China flag",
        "HM" to "Heard and McDonald Islands flag",
        "HN" to "Honduras flag",
        "HR" to "Croatia flag",
        "HT" to "Haiti flag",
        "HU" to "Hungary flag",
        "IC" to "Canary Islands flag",
        "ID" to "Indonesia flag",
        "IE" to "Ireland flag",
        "IL" to "Israel flag",
        "IM" to "Isle of Man flag",
        "IN" to "India flag",
        "IO" to "British Indian Ocean Territory flag",
        "IQ" to "Iraq flag",
        "IR" to "Iran flag",
        "IS" to "Iceland flag",
        "IT" to "Italy flag",
        "JE" to "Jersey flag",
        "JM" to "Jamaica flag",
        "JO" to "Jordan flag",
        "JP" to "Japan flag",
        "KE" to "Kenya flag",
        "KG" to "Kyrgyzstan flag",
        "KH" to "Cambodia flag",
        "KI" to "Kiribati flag",
        "KM" to "Comoros flag",
        "KN" to "St. Kitts and Nevis flag",
        "KP" to "North Korea flag",
        "KR" to "South Korea flag",
        "KW" to "Kuwait flag",
        "KY" to "Cayman Islands flag",
        "KZ" to "Kazakhstan flag",
        "LA" to "Laos flag",
        "LB" to "Lebanon flag",
        "LC" to "St. Lucia flag",
        "LI" to "Liechtenstein flag",
        "LK" to "Sri Lanka flag",
        "LR" to "Liberia flag",
        "LS" to "Lesotho flag",
        "LT" to "Lithuania flag",
        "LU" to "Luxembourg flag",
        "LV" to "Latvia flag",
        "LY" to "Libya flag",
        "MA" to "Morocco flag",
        "MC" to "Monaco flag",
        "MD" to "Moldova flag",
        "ME" to "Montenegro flag",
        "MF" to "St. Martin flag",
        "MG" to "Madagascar flag",
        "MH" to "Marshall Islands flag",
        "MK" to "North Macedonia flag",
        "ML" to "Mali flag",
        "MM" to "Myanmar (Burma) flag",
        "MN" to "Mongolia flag",
        "MO" to "Macao SAR China flag",
        "MP" to "Northern Mariana Islands flag",
        "MQ" to "Martinique flag",
        "MR" to "Mauritania flag",
        "MS" to "Montserrat flag",
        "MT" to "Malta flag",
        "MU" to "Mauritius flag",
        "MV" to "Maldives flag",
        "MW" to "Malawi flag",
        "MX" to "Mexico flag",
        "MY" to "Malaysia flag",
        "MZ" to "Mozambique flag",
        "NA" to "Namibia flag",
        "NC" to "New Caledonia flag",
        "NE" to "Niger flag",
        "NF" to "Norfolk Island flag",
        "NG" to "Nigeria flag",
        "NI" to "Nicaragua flag",
        "NL" to "Netherlands flag",
        "NO" to "Norway flag",
        "NP" to "Nepal flag",
        "NR" to "Nauru flag",
        "NU" to "Niue flag",
        "NZ" to "New Zealand flag",
        "OM" to "Oman flag",
        "PA" to "Panama flag",
        "PE" to "Peru flag",
        "PF" to "French Polynesia flag",
        "PG" to "Papua New Guinea flag",
        "PH" to "Philippines flag",
        "PK" to "Pakistan flag",
        "PL" to "Poland flag",
        "PM" to "St. Pierre and Miquelon flag",
        "PN" to "Pitcairn Islands flag",
        "PR" to "Puerto Rico flag",
        "PS" to "Palestinian Territories flag",
        "PT" to "Portugal flag",
        "PW" to "Palau flag",
        "PY" to "Paraguay flag",
        "QA" to "Qatar flag",
        "RE" to "Reunion flag",
        "RO" to "Romania flag",
        "RS" to "Serbia flag",
        "RU" to "Russia flag",
        "RW" to "Rwanda flag",
        "SA" to "Saudi Arabia flag",
        "SB" to "Solomon Islands flag",
        "SC" to "Seychelles flag",
        "SD" to "Sudan flag",
        "SE" to "Sweden flag",
        "SG" to "Singapore flag",
        "SH" to "St. Helena flag",
        "SI" to "Slovenia flag",
        "SJ" to "Svalbard and Jan Mayen flag",
        "SK" to "Slovakia flag",
        "SL" to "Sierra Leone flag",
        "SM" to "San Marino flag",
        "SN" to "Senegal flag",
        "SO" to "Somalia flag",
        "SR" to "Suriname flag",
        "SS" to "South Sudan flag",
        "ST" to "Sao Tome and Principe flag",
        "SV" to "El Salvador flag",
        "SX" to "Sint Maarten flag",
        "SY" to "Syria flag",
        "SZ" to "Eswatini flag",
        "TA" to "Tristan da Cunha flag",
        "TC" to "Turks and Caicos Islands flag",
        "TD" to "Chad flag",
        "TF" to "French Southern Territories flag",
        "TG" to "Togo flag",
        "TH" to "Thailand flag",
        "TJ" to "Tajikistan flag",
        "TK" to "Tokelau flag",
        "TL" to "Timor-Leste flag",
        "TM" to "Turkmenistan flag",
        "TN" to "Tunisia flag",
        "TO" to "Tonga flag",
        "TR" to "Turkiye flag",
        "TT" to "Trinidad and Tobago flag",
        "TV" to "Tuvalu flag",
        "TW" to "Taiwan flag",
        "TZ" to "Tanzania flag",
        "UA" to "Ukraine flag",
        "UG" to "Uganda flag",
        "UM" to "U.S. Outlying Islands flag",
        "UN" to "United Nations flag",
        "US" to "United States flag",
        "UY" to "Uruguay flag",
        "UZ" to "Uzbekistan flag",
        "VA" to "Vatican City flag",
        "VC" to "St. Vincent and Grenadines flag",
        "VE" to "Venezuela flag",
        "VG" to "British Virgin Islands flag",
        "VI" to "U.S. Virgin Islands flag",
        "VN" to "Vietnam flag",
        "VU" to "Vanuatu flag",
        "WF" to "Wallis and Futuna flag",
        "WS" to "Samoa flag",
        "XK" to "Kosovo flag",
        "YE" to "Yemen flag",
        "YT" to "Mayotte flag",
        "ZA" to "South Africa flag",
        "ZM" to "Zambia flag",
        "ZW" to "Zimbabwe flag",
    )

    // Codepoint -> spoken description, generated from emoji-test.txt: every
    // fully-qualified emoji reducible to one base code point (a bare code
    // point, or a code point plus the U+FE0F variation selector).
    private val NAMES: Map<Int, String> = mapOf(
        0xA9 to "copyright", // ©
        0xAE to "registered", // ®
        0x203C to "double exclamation mark", // ‼
        0x2049 to "exclamation question mark", // ⁉
        0x2122 to "trade mark", // ™
        0x2139 to "information", // ℹ
        0x2194 to "left-right arrow", // ↔
        0x2195 to "up-down arrow", // ↕
        0x2196 to "up-left arrow", // ↖
        0x2197 to "up-right arrow", // ↗
        0x2198 to "down-right arrow", // ↘
        0x2199 to "down-left arrow", // ↙
        0x21A9 to "right arrow curving left", // ↩
        0x21AA to "left arrow curving right", // ↪
        0x231A to "watch", // ⌚
        0x231B to "hourglass done", // ⌛
        0x2328 to "keyboard", // ⌨
        0x23CF to "eject button", // ⏏
        0x23E9 to "fast-forward button", // ⏩
        0x23EA to "fast reverse button", // ⏪
        0x23EB to "fast up button", // ⏫
        0x23EC to "fast down button", // ⏬
        0x23ED to "next track button", // ⏭
        0x23EE to "last track button", // ⏮
        0x23EF to "play or pause button", // ⏯
        0x23F0 to "alarm clock", // ⏰
        0x23F1 to "stopwatch", // ⏱
        0x23F2 to "timer clock", // ⏲
        0x23F3 to "hourglass not done", // ⏳
        0x23F8 to "pause button", // ⏸
        0x23F9 to "stop button", // ⏹
        0x23FA to "record button", // ⏺
        0x24C2 to "circled M", // Ⓜ
        0x25AA to "black small square", // ▪
        0x25AB to "white small square", // ▫
        0x25B6 to "play button", // ▶
        0x25C0 to "reverse button", // ◀
        0x25FB to "white medium square", // ◻
        0x25FC to "black medium square", // ◼
        0x25FD to "white medium-small square", // ◽
        0x25FE to "black medium-small square", // ◾
        0x2600 to "sun", // ☀
        0x2601 to "cloud", // ☁
        0x2602 to "umbrella", // ☂
        0x2603 to "snowman", // ☃
        0x2604 to "comet", // ☄
        0x260E to "telephone", // ☎
        0x2611 to "check box with check", // ☑
        0x2614 to "umbrella with rain drops", // ☔
        0x2615 to "hot beverage", // ☕
        0x2618 to "shamrock", // ☘
        0x261D to "index pointing up", // ☝
        0x2620 to "skull and crossbones", // ☠
        0x2622 to "radioactive", // ☢
        0x2623 to "biohazard", // ☣
        0x2626 to "orthodox cross", // ☦
        0x262A to "star and crescent", // ☪
        0x262E to "peace symbol", // ☮
        0x262F to "yin yang", // ☯
        0x2638 to "wheel of dharma", // ☸
        0x2639 to "frowning face", // ☹
        0x263A to "smiling face", // ☺
        0x2640 to "female sign", // ♀
        0x2642 to "male sign", // ♂
        0x2648 to "Aries", // ♈
        0x2649 to "Taurus", // ♉
        0x264A to "Gemini", // ♊
        0x264B to "Cancer", // ♋
        0x264C to "Leo", // ♌
        0x264D to "Virgo", // ♍
        0x264E to "Libra", // ♎
        0x264F to "Scorpio", // ♏
        0x2650 to "Sagittarius", // ♐
        0x2651 to "Capricorn", // ♑
        0x2652 to "Aquarius", // ♒
        0x2653 to "Pisces", // ♓
        0x265F to "chess pawn", // ♟
        0x2660 to "spade suit", // ♠
        0x2663 to "club suit", // ♣
        0x2665 to "heart suit", // ♥
        0x2666 to "diamond suit", // ♦
        0x2668 to "hot springs", // ♨
        0x267B to "recycling symbol", // ♻
        0x267E to "infinity", // ♾
        0x267F to "wheelchair symbol", // ♿
        0x2692 to "hammer and pick", // ⚒
        0x2693 to "anchor", // ⚓
        0x2694 to "crossed swords", // ⚔
        0x2695 to "medical symbol", // ⚕
        0x2696 to "balance scale", // ⚖
        0x2697 to "alembic", // ⚗
        0x2699 to "gear", // ⚙
        0x269B to "atom symbol", // ⚛
        0x269C to "fleur-de-lis", // ⚜
        0x26A0 to "warning", // ⚠
        0x26A1 to "high voltage", // ⚡
        0x26A7 to "transgender symbol", // ⚧
        0x26AA to "white circle", // ⚪
        0x26AB to "black circle", // ⚫
        0x26B0 to "coffin", // ⚰
        0x26B1 to "funeral urn", // ⚱
        0x26BD to "soccer ball", // ⚽
        0x26BE to "baseball", // ⚾
        0x26C4 to "snowman without snow", // ⛄
        0x26C5 to "sun behind cloud", // ⛅
        0x26C8 to "cloud with lightning and rain", // ⛈
        0x26CE to "Ophiuchus", // ⛎
        0x26CF to "pick", // ⛏
        0x26D1 to "rescue worker's helmet", // ⛑
        0x26D3 to "chains", // ⛓
        0x26D4 to "no entry", // ⛔
        0x26E9 to "shinto shrine", // ⛩
        0x26EA to "church", // ⛪
        0x26F0 to "mountain", // ⛰
        0x26F1 to "umbrella on ground", // ⛱
        0x26F2 to "fountain", // ⛲
        0x26F3 to "flag in hole", // ⛳
        0x26F4 to "ferry", // ⛴
        0x26F5 to "sailboat", // ⛵
        0x26F7 to "skier", // ⛷
        0x26F8 to "ice skate", // ⛸
        0x26F9 to "person bouncing ball", // ⛹
        0x26FA to "tent", // ⛺
        0x26FD to "fuel pump", // ⛽
        0x2702 to "scissors", // ✂
        0x2705 to "check mark button", // ✅
        0x2708 to "airplane", // ✈
        0x2709 to "envelope", // ✉
        0x270A to "raised fist", // ✊
        0x270B to "raised hand", // ✋
        0x270C to "victory hand", // ✌
        0x270D to "writing hand", // ✍
        0x270F to "pencil", // ✏
        0x2712 to "black nib", // ✒
        0x2714 to "check mark", // ✔
        0x2716 to "multiply", // ✖
        0x271D to "latin cross", // ✝
        0x2721 to "star of David", // ✡
        0x2728 to "sparkles", // ✨
        0x2733 to "eight-spoked asterisk", // ✳
        0x2734 to "eight-pointed star", // ✴
        0x2744 to "snowflake", // ❄
        0x2747 to "sparkle", // ❇
        0x274C to "cross mark", // ❌
        0x274E to "cross mark button", // ❎
        0x2753 to "red question mark", // ❓
        0x2754 to "white question mark", // ❔
        0x2755 to "white exclamation mark", // ❕
        0x2757 to "red exclamation mark", // ❗
        0x2763 to "heart exclamation", // ❣
        0x2764 to "red heart", // ❤
        0x2795 to "plus", // ➕
        0x2796 to "minus", // ➖
        0x2797 to "divide", // ➗
        0x27A1 to "right arrow", // ➡
        0x27B0 to "curly loop", // ➰
        0x27BF to "double curly loop", // ➿
        0x2934 to "right arrow curving up", // ⤴
        0x2935 to "right arrow curving down", // ⤵
        0x2B05 to "left arrow", // ⬅
        0x2B06 to "up arrow", // ⬆
        0x2B07 to "down arrow", // ⬇
        0x2B1B to "black large square", // ⬛
        0x2B1C to "white large square", // ⬜
        0x2B50 to "star", // ⭐
        0x2B55 to "hollow red circle", // ⭕
        0x3030 to "wavy dash", // 〰
        0x303D to "part alternation mark", // 〽
        0x3297 to "Japanese “congratulations” button", // ㊗
        0x3299 to "Japanese “secret” button", // ㊙
        0x1F004 to "mahjong red dragon", // 🀄
        0x1F0CF to "joker", // 🃏
        0x1F170 to "A button (blood type)", // 🅰
        0x1F171 to "B button (blood type)", // 🅱
        0x1F17E to "O button (blood type)", // 🅾
        0x1F17F to "P button", // 🅿
        0x1F18E to "AB button (blood type)", // 🆎
        0x1F191 to "CL button", // 🆑
        0x1F192 to "COOL button", // 🆒
        0x1F193 to "FREE button", // 🆓
        0x1F194 to "ID button", // 🆔
        0x1F195 to "NEW button", // 🆕
        0x1F196 to "NG button", // 🆖
        0x1F197 to "OK button", // 🆗
        0x1F198 to "SOS button", // 🆘
        0x1F199 to "UP! button", // 🆙
        0x1F19A to "VS button", // 🆚
        0x1F201 to "Japanese “here” button", // 🈁
        0x1F202 to "Japanese “service charge” button", // 🈂
        0x1F21A to "Japanese “free of charge” button", // 🈚
        0x1F22F to "Japanese “reserved” button", // 🈯
        0x1F232 to "Japanese “prohibited” button", // 🈲
        0x1F233 to "Japanese “vacancy” button", // 🈳
        0x1F234 to "Japanese “passing grade” button", // 🈴
        0x1F235 to "Japanese “no vacancy” button", // 🈵
        0x1F236 to "Japanese “not free of charge” button", // 🈶
        0x1F237 to "Japanese “monthly amount” button", // 🈷
        0x1F238 to "Japanese “application” button", // 🈸
        0x1F239 to "Japanese “discount” button", // 🈹
        0x1F23A to "Japanese “open for business” button", // 🈺
        0x1F250 to "Japanese “bargain” button", // 🉐
        0x1F251 to "Japanese “acceptable” button", // 🉑
        0x1F300 to "cyclone", // 🌀
        0x1F301 to "foggy", // 🌁
        0x1F302 to "closed umbrella", // 🌂
        0x1F303 to "night with stars", // 🌃
        0x1F304 to "sunrise over mountains", // 🌄
        0x1F305 to "sunrise", // 🌅
        0x1F306 to "cityscape at dusk", // 🌆
        0x1F307 to "sunset", // 🌇
        0x1F308 to "rainbow", // 🌈
        0x1F309 to "bridge at night", // 🌉
        0x1F30A to "water wave", // 🌊
        0x1F30B to "volcano", // 🌋
        0x1F30C to "milky way", // 🌌
        0x1F30D to "globe showing Europe-Africa", // 🌍
        0x1F30E to "globe showing Americas", // 🌎
        0x1F30F to "globe showing Asia-Australia", // 🌏
        0x1F310 to "globe with meridians", // 🌐
        0x1F311 to "new moon", // 🌑
        0x1F312 to "waxing crescent moon", // 🌒
        0x1F313 to "first quarter moon", // 🌓
        0x1F314 to "waxing gibbous moon", // 🌔
        0x1F315 to "full moon", // 🌕
        0x1F316 to "waning gibbous moon", // 🌖
        0x1F317 to "last quarter moon", // 🌗
        0x1F318 to "waning crescent moon", // 🌘
        0x1F319 to "crescent moon", // 🌙
        0x1F31A to "new moon face", // 🌚
        0x1F31B to "first quarter moon face", // 🌛
        0x1F31C to "last quarter moon face", // 🌜
        0x1F31D to "full moon face", // 🌝
        0x1F31E to "sun with face", // 🌞
        0x1F31F to "glowing star", // 🌟
        0x1F320 to "shooting star", // 🌠
        0x1F321 to "thermometer", // 🌡
        0x1F324 to "sun behind small cloud", // 🌤
        0x1F325 to "sun behind large cloud", // 🌥
        0x1F326 to "sun behind rain cloud", // 🌦
        0x1F327 to "cloud with rain", // 🌧
        0x1F328 to "cloud with snow", // 🌨
        0x1F329 to "cloud with lightning", // 🌩
        0x1F32A to "tornado", // 🌪
        0x1F32B to "fog", // 🌫
        0x1F32C to "wind face", // 🌬
        0x1F32D to "hot dog", // 🌭
        0x1F32E to "taco", // 🌮
        0x1F32F to "burrito", // 🌯
        0x1F330 to "chestnut", // 🌰
        0x1F331 to "seedling", // 🌱
        0x1F332 to "evergreen tree", // 🌲
        0x1F333 to "deciduous tree", // 🌳
        0x1F334 to "palm tree", // 🌴
        0x1F335 to "cactus", // 🌵
        0x1F336 to "hot pepper", // 🌶
        0x1F337 to "tulip", // 🌷
        0x1F338 to "cherry blossom", // 🌸
        0x1F339 to "rose", // 🌹
        0x1F33A to "hibiscus", // 🌺
        0x1F33B to "sunflower", // 🌻
        0x1F33C to "blossom", // 🌼
        0x1F33D to "ear of corn", // 🌽
        0x1F33E to "sheaf of rice", // 🌾
        0x1F33F to "herb", // 🌿
        0x1F340 to "four leaf clover", // 🍀
        0x1F341 to "maple leaf", // 🍁
        0x1F342 to "fallen leaf", // 🍂
        0x1F343 to "leaf fluttering in wind", // 🍃
        0x1F344 to "mushroom", // 🍄
        0x1F345 to "tomato", // 🍅
        0x1F346 to "eggplant", // 🍆
        0x1F347 to "grapes", // 🍇
        0x1F348 to "melon", // 🍈
        0x1F349 to "watermelon", // 🍉
        0x1F34A to "tangerine", // 🍊
        0x1F34B to "lemon", // 🍋
        0x1F34C to "banana", // 🍌
        0x1F34D to "pineapple", // 🍍
        0x1F34E to "red apple", // 🍎
        0x1F34F to "green apple", // 🍏
        0x1F350 to "pear", // 🍐
        0x1F351 to "peach", // 🍑
        0x1F352 to "cherries", // 🍒
        0x1F353 to "strawberry", // 🍓
        0x1F354 to "hamburger", // 🍔
        0x1F355 to "pizza", // 🍕
        0x1F356 to "meat on bone", // 🍖
        0x1F357 to "poultry leg", // 🍗
        0x1F358 to "rice cracker", // 🍘
        0x1F359 to "rice ball", // 🍙
        0x1F35A to "cooked rice", // 🍚
        0x1F35B to "curry rice", // 🍛
        0x1F35C to "steaming bowl", // 🍜
        0x1F35D to "spaghetti", // 🍝
        0x1F35E to "bread", // 🍞
        0x1F35F to "french fries", // 🍟
        0x1F360 to "roasted sweet potato", // 🍠
        0x1F361 to "dango", // 🍡
        0x1F362 to "oden", // 🍢
        0x1F363 to "sushi", // 🍣
        0x1F364 to "fried shrimp", // 🍤
        0x1F365 to "fish cake with swirl", // 🍥
        0x1F366 to "soft ice cream", // 🍦
        0x1F367 to "shaved ice", // 🍧
        0x1F368 to "ice cream", // 🍨
        0x1F369 to "doughnut", // 🍩
        0x1F36A to "cookie", // 🍪
        0x1F36B to "chocolate bar", // 🍫
        0x1F36C to "candy", // 🍬
        0x1F36D to "lollipop", // 🍭
        0x1F36E to "custard", // 🍮
        0x1F36F to "honey pot", // 🍯
        0x1F370 to "shortcake", // 🍰
        0x1F371 to "bento box", // 🍱
        0x1F372 to "pot of food", // 🍲
        0x1F373 to "cooking", // 🍳
        0x1F374 to "fork and knife", // 🍴
        0x1F375 to "teacup without handle", // 🍵
        0x1F376 to "sake", // 🍶
        0x1F377 to "wine glass", // 🍷
        0x1F378 to "cocktail glass", // 🍸
        0x1F379 to "tropical drink", // 🍹
        0x1F37A to "beer mug", // 🍺
        0x1F37B to "clinking beer mugs", // 🍻
        0x1F37C to "baby bottle", // 🍼
        0x1F37D to "fork and knife with plate", // 🍽
        0x1F37E to "bottle with popping cork", // 🍾
        0x1F37F to "popcorn", // 🍿
        0x1F380 to "ribbon", // 🎀
        0x1F381 to "wrapped gift", // 🎁
        0x1F382 to "birthday cake", // 🎂
        0x1F383 to "jack-o-lantern", // 🎃
        0x1F384 to "Christmas tree", // 🎄
        0x1F385 to "Santa Claus", // 🎅
        0x1F386 to "fireworks", // 🎆
        0x1F387 to "sparkler", // 🎇
        0x1F388 to "balloon", // 🎈
        0x1F389 to "party popper", // 🎉
        0x1F38A to "confetti ball", // 🎊
        0x1F38B to "tanabata tree", // 🎋
        0x1F38C to "crossed flags", // 🎌
        0x1F38D to "pine decoration", // 🎍
        0x1F38E to "Japanese dolls", // 🎎
        0x1F38F to "carp streamer", // 🎏
        0x1F390 to "wind chime", // 🎐
        0x1F391 to "moon viewing ceremony", // 🎑
        0x1F392 to "backpack", // 🎒
        0x1F393 to "graduation cap", // 🎓
        0x1F396 to "military medal", // 🎖
        0x1F397 to "reminder ribbon", // 🎗
        0x1F399 to "studio microphone", // 🎙
        0x1F39A to "level slider", // 🎚
        0x1F39B to "control knobs", // 🎛
        0x1F39E to "film frames", // 🎞
        0x1F39F to "admission tickets", // 🎟
        0x1F3A0 to "carousel horse", // 🎠
        0x1F3A1 to "ferris wheel", // 🎡
        0x1F3A2 to "roller coaster", // 🎢
        0x1F3A3 to "fishing pole", // 🎣
        0x1F3A4 to "microphone", // 🎤
        0x1F3A5 to "movie camera", // 🎥
        0x1F3A6 to "cinema", // 🎦
        0x1F3A7 to "headphone", // 🎧
        0x1F3A8 to "artist palette", // 🎨
        0x1F3A9 to "top hat", // 🎩
        0x1F3AA to "circus tent", // 🎪
        0x1F3AB to "ticket", // 🎫
        0x1F3AC to "clapper board", // 🎬
        0x1F3AD to "performing arts", // 🎭
        0x1F3AE to "video game", // 🎮
        0x1F3AF to "bullseye", // 🎯
        0x1F3B0 to "slot machine", // 🎰
        0x1F3B1 to "pool 8 ball", // 🎱
        0x1F3B2 to "game die", // 🎲
        0x1F3B3 to "bowling", // 🎳
        0x1F3B4 to "flower playing cards", // 🎴
        0x1F3B5 to "musical note", // 🎵
        0x1F3B6 to "musical notes", // 🎶
        0x1F3B7 to "saxophone", // 🎷
        0x1F3B8 to "guitar", // 🎸
        0x1F3B9 to "musical keyboard", // 🎹
        0x1F3BA to "trumpet", // 🎺
        0x1F3BB to "violin", // 🎻
        0x1F3BC to "musical score", // 🎼
        0x1F3BD to "running shirt", // 🎽
        0x1F3BE to "tennis", // 🎾
        0x1F3BF to "skis", // 🎿
        0x1F3C0 to "basketball", // 🏀
        0x1F3C1 to "chequered flag", // 🏁
        0x1F3C2 to "snowboarder", // 🏂
        0x1F3C3 to "person running", // 🏃
        0x1F3C4 to "person surfing", // 🏄
        0x1F3C5 to "sports medal", // 🏅
        0x1F3C6 to "trophy", // 🏆
        0x1F3C7 to "horse racing", // 🏇
        0x1F3C8 to "american football", // 🏈
        0x1F3C9 to "rugby football", // 🏉
        0x1F3CA to "person swimming", // 🏊
        0x1F3CB to "person lifting weights", // 🏋
        0x1F3CC to "person golfing", // 🏌
        0x1F3CD to "motorcycle", // 🏍
        0x1F3CE to "racing car", // 🏎
        0x1F3CF to "cricket game", // 🏏
        0x1F3D0 to "volleyball", // 🏐
        0x1F3D1 to "field hockey", // 🏑
        0x1F3D2 to "ice hockey", // 🏒
        0x1F3D3 to "ping pong", // 🏓
        0x1F3D4 to "snow-capped mountain", // 🏔
        0x1F3D5 to "camping", // 🏕
        0x1F3D6 to "beach with umbrella", // 🏖
        0x1F3D7 to "building construction", // 🏗
        0x1F3D8 to "houses", // 🏘
        0x1F3D9 to "cityscape", // 🏙
        0x1F3DA to "derelict house", // 🏚
        0x1F3DB to "classical building", // 🏛
        0x1F3DC to "desert", // 🏜
        0x1F3DD to "desert island", // 🏝
        0x1F3DE to "national park", // 🏞
        0x1F3DF to "stadium", // 🏟
        0x1F3E0 to "house", // 🏠
        0x1F3E1 to "house with garden", // 🏡
        0x1F3E2 to "office building", // 🏢
        0x1F3E3 to "Japanese post office", // 🏣
        0x1F3E4 to "post office", // 🏤
        0x1F3E5 to "hospital", // 🏥
        0x1F3E6 to "bank", // 🏦
        0x1F3E7 to "ATM sign", // 🏧
        0x1F3E8 to "hotel", // 🏨
        0x1F3E9 to "love hotel", // 🏩
        0x1F3EA to "convenience store", // 🏪
        0x1F3EB to "school", // 🏫
        0x1F3EC to "department store", // 🏬
        0x1F3ED to "factory", // 🏭
        0x1F3EE to "red paper lantern", // 🏮
        0x1F3EF to "Japanese castle", // 🏯
        0x1F3F0 to "castle", // 🏰
        0x1F3F3 to "white flag", // 🏳
        0x1F3F4 to "black flag", // 🏴
        0x1F3F5 to "rosette", // 🏵
        0x1F3F7 to "label", // 🏷
        0x1F3F8 to "badminton", // 🏸
        0x1F3F9 to "bow and arrow", // 🏹
        0x1F3FA to "amphora", // 🏺
        0x1F400 to "rat", // 🐀
        0x1F401 to "mouse", // 🐁
        0x1F402 to "ox", // 🐂
        0x1F403 to "water buffalo", // 🐃
        0x1F404 to "cow", // 🐄
        0x1F405 to "tiger", // 🐅
        0x1F406 to "leopard", // 🐆
        0x1F407 to "rabbit", // 🐇
        0x1F408 to "cat", // 🐈
        0x1F409 to "dragon", // 🐉
        0x1F40A to "crocodile", // 🐊
        0x1F40B to "whale", // 🐋
        0x1F40C to "snail", // 🐌
        0x1F40D to "snake", // 🐍
        0x1F40E to "horse", // 🐎
        0x1F40F to "ram", // 🐏
        0x1F410 to "goat", // 🐐
        0x1F411 to "ewe", // 🐑
        0x1F412 to "monkey", // 🐒
        0x1F413 to "rooster", // 🐓
        0x1F414 to "chicken", // 🐔
        0x1F415 to "dog", // 🐕
        0x1F416 to "pig", // 🐖
        0x1F417 to "boar", // 🐗
        0x1F418 to "elephant", // 🐘
        0x1F419 to "octopus", // 🐙
        0x1F41A to "spiral shell", // 🐚
        0x1F41B to "bug", // 🐛
        0x1F41C to "ant", // 🐜
        0x1F41D to "honeybee", // 🐝
        0x1F41E to "lady beetle", // 🐞
        0x1F41F to "fish", // 🐟
        0x1F420 to "tropical fish", // 🐠
        0x1F421 to "blowfish", // 🐡
        0x1F422 to "turtle", // 🐢
        0x1F423 to "hatching chick", // 🐣
        0x1F424 to "baby chick", // 🐤
        0x1F425 to "front-facing baby chick", // 🐥
        0x1F426 to "bird", // 🐦
        0x1F427 to "penguin", // 🐧
        0x1F428 to "koala", // 🐨
        0x1F429 to "poodle", // 🐩
        0x1F42A to "camel", // 🐪
        0x1F42B to "two-hump camel", // 🐫
        0x1F42C to "dolphin", // 🐬
        0x1F42D to "mouse face", // 🐭
        0x1F42E to "cow face", // 🐮
        0x1F42F to "tiger face", // 🐯
        0x1F430 to "rabbit face", // 🐰
        0x1F431 to "cat face", // 🐱
        0x1F432 to "dragon face", // 🐲
        0x1F433 to "spouting whale", // 🐳
        0x1F434 to "horse face", // 🐴
        0x1F435 to "monkey face", // 🐵
        0x1F436 to "dog face", // 🐶
        0x1F437 to "pig face", // 🐷
        0x1F438 to "frog", // 🐸
        0x1F439 to "hamster", // 🐹
        0x1F43A to "wolf", // 🐺
        0x1F43B to "bear", // 🐻
        0x1F43C to "panda", // 🐼
        0x1F43D to "pig nose", // 🐽
        0x1F43E to "paw prints", // 🐾
        0x1F43F to "chipmunk", // 🐿
        0x1F440 to "eyes", // 👀
        0x1F441 to "eye", // 👁
        0x1F442 to "ear", // 👂
        0x1F443 to "nose", // 👃
        0x1F444 to "mouth", // 👄
        0x1F445 to "tongue", // 👅
        0x1F446 to "backhand index pointing up", // 👆
        0x1F447 to "backhand index pointing down", // 👇
        0x1F448 to "backhand index pointing left", // 👈
        0x1F449 to "backhand index pointing right", // 👉
        0x1F44A to "oncoming fist", // 👊
        0x1F44B to "waving hand", // 👋
        0x1F44C to "OK hand", // 👌
        0x1F44D to "thumbs up", // 👍
        0x1F44E to "thumbs down", // 👎
        0x1F44F to "clapping hands", // 👏
        0x1F450 to "open hands", // 👐
        0x1F451 to "crown", // 👑
        0x1F452 to "woman's hat", // 👒
        0x1F453 to "glasses", // 👓
        0x1F454 to "necktie", // 👔
        0x1F455 to "t-shirt", // 👕
        0x1F456 to "jeans", // 👖
        0x1F457 to "dress", // 👗
        0x1F458 to "kimono", // 👘
        0x1F459 to "bikini", // 👙
        0x1F45A to "woman's clothes", // 👚
        0x1F45B to "purse", // 👛
        0x1F45C to "handbag", // 👜
        0x1F45D to "clutch bag", // 👝
        0x1F45E to "man's shoe", // 👞
        0x1F45F to "running shoe", // 👟
        0x1F460 to "high-heeled shoe", // 👠
        0x1F461 to "woman's sandal", // 👡
        0x1F462 to "woman's boot", // 👢
        0x1F463 to "footprints", // 👣
        0x1F464 to "bust in silhouette", // 👤
        0x1F465 to "busts in silhouette", // 👥
        0x1F466 to "boy", // 👦
        0x1F467 to "girl", // 👧
        0x1F468 to "man", // 👨
        0x1F469 to "woman", // 👩
        0x1F46A to "family", // 👪
        0x1F46B to "woman and man holding hands", // 👫
        0x1F46C to "men holding hands", // 👬
        0x1F46D to "women holding hands", // 👭
        0x1F46E to "police officer", // 👮
        0x1F46F to "people with bunny ears", // 👯
        0x1F470 to "person with veil", // 👰
        0x1F471 to "person: blond hair", // 👱
        0x1F472 to "person with skullcap", // 👲
        0x1F473 to "person wearing turban", // 👳
        0x1F474 to "old man", // 👴
        0x1F475 to "old woman", // 👵
        0x1F476 to "baby", // 👶
        0x1F477 to "construction worker", // 👷
        0x1F478 to "princess", // 👸
        0x1F479 to "ogre", // 👹
        0x1F47A to "goblin", // 👺
        0x1F47B to "ghost", // 👻
        0x1F47C to "baby angel", // 👼
        0x1F47D to "alien", // 👽
        0x1F47E to "alien monster", // 👾
        0x1F47F to "angry face with horns", // 👿
        0x1F480 to "skull", // 💀
        0x1F481 to "person tipping hand", // 💁
        0x1F482 to "guard", // 💂
        0x1F483 to "woman dancing", // 💃
        0x1F484 to "lipstick", // 💄
        0x1F485 to "nail polish", // 💅
        0x1F486 to "person getting massage", // 💆
        0x1F487 to "person getting haircut", // 💇
        0x1F488 to "barber pole", // 💈
        0x1F489 to "syringe", // 💉
        0x1F48A to "pill", // 💊
        0x1F48B to "kiss mark", // 💋
        0x1F48C to "love letter", // 💌
        0x1F48D to "ring", // 💍
        0x1F48E to "gem stone", // 💎
        0x1F48F to "kiss", // 💏
        0x1F490 to "bouquet", // 💐
        0x1F491 to "couple with heart", // 💑
        0x1F492 to "wedding", // 💒
        0x1F493 to "beating heart", // 💓
        0x1F494 to "broken heart", // 💔
        0x1F495 to "two hearts", // 💕
        0x1F496 to "sparkling heart", // 💖
        0x1F497 to "growing heart", // 💗
        0x1F498 to "heart with arrow", // 💘
        0x1F499 to "blue heart", // 💙
        0x1F49A to "green heart", // 💚
        0x1F49B to "yellow heart", // 💛
        0x1F49C to "purple heart", // 💜
        0x1F49D to "heart with ribbon", // 💝
        0x1F49E to "revolving hearts", // 💞
        0x1F49F to "heart decoration", // 💟
        0x1F4A0 to "diamond with a dot", // 💠
        0x1F4A1 to "light bulb", // 💡
        0x1F4A2 to "anger symbol", // 💢
        0x1F4A3 to "bomb", // 💣
        0x1F4A4 to "ZZZ", // 💤
        0x1F4A5 to "collision", // 💥
        0x1F4A6 to "sweat droplets", // 💦
        0x1F4A7 to "droplet", // 💧
        0x1F4A8 to "dashing away", // 💨
        0x1F4A9 to "pile of poo", // 💩
        0x1F4AA to "flexed biceps", // 💪
        0x1F4AB to "dizzy", // 💫
        0x1F4AC to "speech balloon", // 💬
        0x1F4AD to "thought balloon", // 💭
        0x1F4AE to "white flower", // 💮
        0x1F4AF to "hundred points", // 💯
        0x1F4B0 to "money bag", // 💰
        0x1F4B1 to "currency exchange", // 💱
        0x1F4B2 to "heavy dollar sign", // 💲
        0x1F4B3 to "credit card", // 💳
        0x1F4B4 to "yen banknote", // 💴
        0x1F4B5 to "dollar banknote", // 💵
        0x1F4B6 to "euro banknote", // 💶
        0x1F4B7 to "pound banknote", // 💷
        0x1F4B8 to "money with wings", // 💸
        0x1F4B9 to "chart increasing with yen", // 💹
        0x1F4BA to "seat", // 💺
        0x1F4BB to "laptop", // 💻
        0x1F4BC to "briefcase", // 💼
        0x1F4BD to "computer disk", // 💽
        0x1F4BE to "floppy disk", // 💾
        0x1F4BF to "optical disk", // 💿
        0x1F4C0 to "dvd", // 📀
        0x1F4C1 to "file folder", // 📁
        0x1F4C2 to "open file folder", // 📂
        0x1F4C3 to "page with curl", // 📃
        0x1F4C4 to "page facing up", // 📄
        0x1F4C5 to "calendar", // 📅
        0x1F4C6 to "tear-off calendar", // 📆
        0x1F4C7 to "card index", // 📇
        0x1F4C8 to "chart increasing", // 📈
        0x1F4C9 to "chart decreasing", // 📉
        0x1F4CA to "bar chart", // 📊
        0x1F4CB to "clipboard", // 📋
        0x1F4CC to "pushpin", // 📌
        0x1F4CD to "round pushpin", // 📍
        0x1F4CE to "paperclip", // 📎
        0x1F4CF to "straight ruler", // 📏
        0x1F4D0 to "triangular ruler", // 📐
        0x1F4D1 to "bookmark tabs", // 📑
        0x1F4D2 to "ledger", // 📒
        0x1F4D3 to "notebook", // 📓
        0x1F4D4 to "notebook with decorative cover", // 📔
        0x1F4D5 to "closed book", // 📕
        0x1F4D6 to "open book", // 📖
        0x1F4D7 to "green book", // 📗
        0x1F4D8 to "blue book", // 📘
        0x1F4D9 to "orange book", // 📙
        0x1F4DA to "books", // 📚
        0x1F4DB to "name badge", // 📛
        0x1F4DC to "scroll", // 📜
        0x1F4DD to "memo", // 📝
        0x1F4DE to "telephone receiver", // 📞
        0x1F4DF to "pager", // 📟
        0x1F4E0 to "fax machine", // 📠
        0x1F4E1 to "satellite antenna", // 📡
        0x1F4E2 to "loudspeaker", // 📢
        0x1F4E3 to "megaphone", // 📣
        0x1F4E4 to "outbox tray", // 📤
        0x1F4E5 to "inbox tray", // 📥
        0x1F4E6 to "package", // 📦
        0x1F4E7 to "e-mail", // 📧
        0x1F4E8 to "incoming envelope", // 📨
        0x1F4E9 to "envelope with arrow", // 📩
        0x1F4EA to "closed mailbox with lowered flag", // 📪
        0x1F4EB to "closed mailbox with raised flag", // 📫
        0x1F4EC to "open mailbox with raised flag", // 📬
        0x1F4ED to "open mailbox with lowered flag", // 📭
        0x1F4EE to "postbox", // 📮
        0x1F4EF to "postal horn", // 📯
        0x1F4F0 to "newspaper", // 📰
        0x1F4F1 to "mobile phone", // 📱
        0x1F4F2 to "mobile phone with arrow", // 📲
        0x1F4F3 to "vibration mode", // 📳
        0x1F4F4 to "mobile phone off", // 📴
        0x1F4F5 to "no mobile phones", // 📵
        0x1F4F6 to "antenna bars", // 📶
        0x1F4F7 to "camera", // 📷
        0x1F4F8 to "camera with flash", // 📸
        0x1F4F9 to "video camera", // 📹
        0x1F4FA to "television", // 📺
        0x1F4FB to "radio", // 📻
        0x1F4FC to "videocassette", // 📼
        0x1F4FD to "film projector", // 📽
        0x1F4FF to "prayer beads", // 📿
        0x1F500 to "shuffle tracks button", // 🔀
        0x1F501 to "repeat button", // 🔁
        0x1F502 to "repeat single button", // 🔂
        0x1F503 to "clockwise vertical arrows", // 🔃
        0x1F504 to "counterclockwise arrows button", // 🔄
        0x1F505 to "dim button", // 🔅
        0x1F506 to "bright button", // 🔆
        0x1F507 to "muted speaker", // 🔇
        0x1F508 to "speaker low volume", // 🔈
        0x1F509 to "speaker medium volume", // 🔉
        0x1F50A to "speaker high volume", // 🔊
        0x1F50B to "battery", // 🔋
        0x1F50C to "electric plug", // 🔌
        0x1F50D to "magnifying glass tilted left", // 🔍
        0x1F50E to "magnifying glass tilted right", // 🔎
        0x1F50F to "locked with pen", // 🔏
        0x1F510 to "locked with key", // 🔐
        0x1F511 to "key", // 🔑
        0x1F512 to "locked", // 🔒
        0x1F513 to "unlocked", // 🔓
        0x1F514 to "bell", // 🔔
        0x1F515 to "bell with slash", // 🔕
        0x1F516 to "bookmark", // 🔖
        0x1F517 to "link", // 🔗
        0x1F518 to "radio button", // 🔘
        0x1F519 to "BACK arrow", // 🔙
        0x1F51A to "END arrow", // 🔚
        0x1F51B to "ON! arrow", // 🔛
        0x1F51C to "SOON arrow", // 🔜
        0x1F51D to "TOP arrow", // 🔝
        0x1F51E to "no one under eighteen", // 🔞
        0x1F51F to "keycap: 10", // 🔟
        0x1F520 to "input latin uppercase", // 🔠
        0x1F521 to "input latin lowercase", // 🔡
        0x1F522 to "input numbers", // 🔢
        0x1F523 to "input symbols", // 🔣
        0x1F524 to "input latin letters", // 🔤
        0x1F525 to "fire", // 🔥
        0x1F526 to "flashlight", // 🔦
        0x1F527 to "wrench", // 🔧
        0x1F528 to "hammer", // 🔨
        0x1F529 to "nut and bolt", // 🔩
        0x1F52A to "kitchen knife", // 🔪
        0x1F52B to "water pistol", // 🔫
        0x1F52C to "microscope", // 🔬
        0x1F52D to "telescope", // 🔭
        0x1F52E to "crystal ball", // 🔮
        0x1F52F to "dotted six-pointed star", // 🔯
        0x1F530 to "Japanese symbol for beginner", // 🔰
        0x1F531 to "trident emblem", // 🔱
        0x1F532 to "black square button", // 🔲
        0x1F533 to "white square button", // 🔳
        0x1F534 to "red circle", // 🔴
        0x1F535 to "blue circle", // 🔵
        0x1F536 to "large orange diamond", // 🔶
        0x1F537 to "large blue diamond", // 🔷
        0x1F538 to "small orange diamond", // 🔸
        0x1F539 to "small blue diamond", // 🔹
        0x1F53A to "red triangle pointed up", // 🔺
        0x1F53B to "red triangle pointed down", // 🔻
        0x1F53C to "upwards button", // 🔼
        0x1F53D to "downwards button", // 🔽
        0x1F549 to "om", // 🕉
        0x1F54A to "dove", // 🕊
        0x1F54B to "kaaba", // 🕋
        0x1F54C to "mosque", // 🕌
        0x1F54D to "synagogue", // 🕍
        0x1F54E to "menorah", // 🕎
        0x1F550 to "one o'clock", // 🕐
        0x1F551 to "two o'clock", // 🕑
        0x1F552 to "three o'clock", // 🕒
        0x1F553 to "four o'clock", // 🕓
        0x1F554 to "five o'clock", // 🕔
        0x1F555 to "six o'clock", // 🕕
        0x1F556 to "seven o'clock", // 🕖
        0x1F557 to "eight o'clock", // 🕗
        0x1F558 to "nine o'clock", // 🕘
        0x1F559 to "ten o'clock", // 🕙
        0x1F55A to "eleven o'clock", // 🕚
        0x1F55B to "twelve o'clock", // 🕛
        0x1F55C to "one-thirty", // 🕜
        0x1F55D to "two-thirty", // 🕝
        0x1F55E to "three-thirty", // 🕞
        0x1F55F to "four-thirty", // 🕟
        0x1F560 to "five-thirty", // 🕠
        0x1F561 to "six-thirty", // 🕡
        0x1F562 to "seven-thirty", // 🕢
        0x1F563 to "eight-thirty", // 🕣
        0x1F564 to "nine-thirty", // 🕤
        0x1F565 to "ten-thirty", // 🕥
        0x1F566 to "eleven-thirty", // 🕦
        0x1F567 to "twelve-thirty", // 🕧
        0x1F56F to "candle", // 🕯
        0x1F570 to "mantelpiece clock", // 🕰
        0x1F573 to "hole", // 🕳
        0x1F574 to "person in suit levitating", // 🕴
        0x1F575 to "detective", // 🕵
        0x1F576 to "sunglasses", // 🕶
        0x1F577 to "spider", // 🕷
        0x1F578 to "spider web", // 🕸
        0x1F579 to "joystick", // 🕹
        0x1F57A to "man dancing", // 🕺
        0x1F587 to "linked paperclips", // 🖇
        0x1F58A to "pen", // 🖊
        0x1F58B to "fountain pen", // 🖋
        0x1F58C to "paintbrush", // 🖌
        0x1F58D to "crayon", // 🖍
        0x1F590 to "hand with fingers splayed", // 🖐
        0x1F595 to "middle finger", // 🖕
        0x1F596 to "vulcan salute", // 🖖
        0x1F5A4 to "black heart", // 🖤
        0x1F5A5 to "desktop computer", // 🖥
        0x1F5A8 to "printer", // 🖨
        0x1F5B1 to "computer mouse", // 🖱
        0x1F5B2 to "trackball", // 🖲
        0x1F5BC to "framed picture", // 🖼
        0x1F5C2 to "card index dividers", // 🗂
        0x1F5C3 to "card file box", // 🗃
        0x1F5C4 to "file cabinet", // 🗄
        0x1F5D1 to "wastebasket", // 🗑
        0x1F5D2 to "spiral notepad", // 🗒
        0x1F5D3 to "spiral calendar", // 🗓
        0x1F5DC to "clamp", // 🗜
        0x1F5DD to "old key", // 🗝
        0x1F5DE to "rolled-up newspaper", // 🗞
        0x1F5E1 to "dagger", // 🗡
        0x1F5E3 to "speaking head", // 🗣
        0x1F5E8 to "left speech bubble", // 🗨
        0x1F5EF to "right anger bubble", // 🗯
        0x1F5F3 to "ballot box with ballot", // 🗳
        0x1F5FA to "world map", // 🗺
        0x1F5FB to "mount fuji", // 🗻
        0x1F5FC to "Tokyo tower", // 🗼
        0x1F5FD to "Statue of Liberty", // 🗽
        0x1F5FE to "map of Japan", // 🗾
        0x1F5FF to "moai", // 🗿
        0x1F600 to "grinning face", // 😀
        0x1F601 to "beaming face with smiling eyes", // 😁
        0x1F602 to "face with tears of joy", // 😂
        0x1F603 to "grinning face with big eyes", // 😃
        0x1F604 to "grinning face with smiling eyes", // 😄
        0x1F605 to "grinning face with sweat", // 😅
        0x1F606 to "grinning squinting face", // 😆
        0x1F607 to "smiling face with halo", // 😇
        0x1F608 to "smiling face with horns", // 😈
        0x1F609 to "winking face", // 😉
        0x1F60A to "smiling face with smiling eyes", // 😊
        0x1F60B to "face savoring food", // 😋
        0x1F60C to "relieved face", // 😌
        0x1F60D to "smiling face with heart-eyes", // 😍
        0x1F60E to "smiling face with sunglasses", // 😎
        0x1F60F to "smirking face", // 😏
        0x1F610 to "neutral face", // 😐
        0x1F611 to "expressionless face", // 😑
        0x1F612 to "unamused face", // 😒
        0x1F613 to "downcast face with sweat", // 😓
        0x1F614 to "pensive face", // 😔
        0x1F615 to "confused face", // 😕
        0x1F616 to "confounded face", // 😖
        0x1F617 to "kissing face", // 😗
        0x1F618 to "face blowing a kiss", // 😘
        0x1F619 to "kissing face with smiling eyes", // 😙
        0x1F61A to "kissing face with closed eyes", // 😚
        0x1F61B to "face with tongue", // 😛
        0x1F61C to "winking face with tongue", // 😜
        0x1F61D to "squinting face with tongue", // 😝
        0x1F61E to "disappointed face", // 😞
        0x1F61F to "worried face", // 😟
        0x1F620 to "angry face", // 😠
        0x1F621 to "enraged face", // 😡
        0x1F622 to "crying face", // 😢
        0x1F623 to "persevering face", // 😣
        0x1F624 to "face with steam from nose", // 😤
        0x1F625 to "sad but relieved face", // 😥
        0x1F626 to "frowning face with open mouth", // 😦
        0x1F627 to "anguished face", // 😧
        0x1F628 to "fearful face", // 😨
        0x1F629 to "weary face", // 😩
        0x1F62A to "sleepy face", // 😪
        0x1F62B to "tired face", // 😫
        0x1F62C to "grimacing face", // 😬
        0x1F62D to "loudly crying face", // 😭
        0x1F62E to "face with open mouth", // 😮
        0x1F62F to "hushed face", // 😯
        0x1F630 to "anxious face with sweat", // 😰
        0x1F631 to "face screaming in fear", // 😱
        0x1F632 to "astonished face", // 😲
        0x1F633 to "flushed face", // 😳
        0x1F634 to "sleeping face", // 😴
        0x1F635 to "face with crossed-out eyes", // 😵
        0x1F636 to "face without mouth", // 😶
        0x1F637 to "face with medical mask", // 😷
        0x1F638 to "grinning cat with smiling eyes", // 😸
        0x1F639 to "cat with tears of joy", // 😹
        0x1F63A to "grinning cat", // 😺
        0x1F63B to "smiling cat with heart-eyes", // 😻
        0x1F63C to "cat with wry smile", // 😼
        0x1F63D to "kissing cat", // 😽
        0x1F63E to "pouting cat", // 😾
        0x1F63F to "crying cat", // 😿
        0x1F640 to "weary cat", // 🙀
        0x1F641 to "slightly frowning face", // 🙁
        0x1F642 to "slightly smiling face", // 🙂
        0x1F643 to "upside-down face", // 🙃
        0x1F644 to "face with rolling eyes", // 🙄
        0x1F645 to "person gesturing NO", // 🙅
        0x1F646 to "person gesturing OK", // 🙆
        0x1F647 to "person bowing", // 🙇
        0x1F648 to "see-no-evil monkey", // 🙈
        0x1F649 to "hear-no-evil monkey", // 🙉
        0x1F64A to "speak-no-evil monkey", // 🙊
        0x1F64B to "person raising hand", // 🙋
        0x1F64C to "raising hands", // 🙌
        0x1F64D to "person frowning", // 🙍
        0x1F64E to "person pouting", // 🙎
        0x1F64F to "folded hands", // 🙏
        0x1F680 to "rocket", // 🚀
        0x1F681 to "helicopter", // 🚁
        0x1F682 to "locomotive", // 🚂
        0x1F683 to "railway car", // 🚃
        0x1F684 to "high-speed train", // 🚄
        0x1F685 to "bullet train", // 🚅
        0x1F686 to "train", // 🚆
        0x1F687 to "metro", // 🚇
        0x1F688 to "light rail", // 🚈
        0x1F689 to "station", // 🚉
        0x1F68A to "tram", // 🚊
        0x1F68B to "tram car", // 🚋
        0x1F68C to "bus", // 🚌
        0x1F68D to "oncoming bus", // 🚍
        0x1F68E to "trolleybus", // 🚎
        0x1F68F to "bus stop", // 🚏
        0x1F690 to "minibus", // 🚐
        0x1F691 to "ambulance", // 🚑
        0x1F692 to "fire engine", // 🚒
        0x1F693 to "police car", // 🚓
        0x1F694 to "oncoming police car", // 🚔
        0x1F695 to "taxi", // 🚕
        0x1F696 to "oncoming taxi", // 🚖
        0x1F697 to "automobile", // 🚗
        0x1F698 to "oncoming automobile", // 🚘
        0x1F699 to "sport utility vehicle", // 🚙
        0x1F69A to "delivery truck", // 🚚
        0x1F69B to "articulated lorry", // 🚛
        0x1F69C to "tractor", // 🚜
        0x1F69D to "monorail", // 🚝
        0x1F69E to "mountain railway", // 🚞
        0x1F69F to "suspension railway", // 🚟
        0x1F6A0 to "mountain cableway", // 🚠
        0x1F6A1 to "aerial tramway", // 🚡
        0x1F6A2 to "ship", // 🚢
        0x1F6A3 to "person rowing boat", // 🚣
        0x1F6A4 to "speedboat", // 🚤
        0x1F6A5 to "horizontal traffic light", // 🚥
        0x1F6A6 to "vertical traffic light", // 🚦
        0x1F6A7 to "construction", // 🚧
        0x1F6A8 to "police car light", // 🚨
        0x1F6A9 to "triangular flag", // 🚩
        0x1F6AA to "door", // 🚪
        0x1F6AB to "prohibited", // 🚫
        0x1F6AC to "cigarette", // 🚬
        0x1F6AD to "no smoking", // 🚭
        0x1F6AE to "litter in bin sign", // 🚮
        0x1F6AF to "no littering", // 🚯
        0x1F6B0 to "potable water", // 🚰
        0x1F6B1 to "non-potable water", // 🚱
        0x1F6B2 to "bicycle", // 🚲
        0x1F6B3 to "no bicycles", // 🚳
        0x1F6B4 to "person biking", // 🚴
        0x1F6B5 to "person mountain biking", // 🚵
        0x1F6B6 to "person walking", // 🚶
        0x1F6B7 to "no pedestrians", // 🚷
        0x1F6B8 to "children crossing", // 🚸
        0x1F6B9 to "men's room", // 🚹
        0x1F6BA to "women's room", // 🚺
        0x1F6BB to "restroom", // 🚻
        0x1F6BC to "baby symbol", // 🚼
        0x1F6BD to "toilet", // 🚽
        0x1F6BE to "water closet", // 🚾
        0x1F6BF to "shower", // 🚿
        0x1F6C0 to "person taking bath", // 🛀
        0x1F6C1 to "bathtub", // 🛁
        0x1F6C2 to "passport control", // 🛂
        0x1F6C3 to "customs", // 🛃
        0x1F6C4 to "baggage claim", // 🛄
        0x1F6C5 to "left luggage", // 🛅
        0x1F6CB to "couch and lamp", // 🛋
        0x1F6CC to "person in bed", // 🛌
        0x1F6CD to "shopping bags", // 🛍
        0x1F6CE to "bellhop bell", // 🛎
        0x1F6CF to "bed", // 🛏
        0x1F6D0 to "place of worship", // 🛐
        0x1F6D1 to "stop sign", // 🛑
        0x1F6D2 to "shopping cart", // 🛒
        0x1F6D5 to "hindu temple", // 🛕
        0x1F6D6 to "hut", // 🛖
        0x1F6D7 to "elevator", // 🛗
        0x1F6DC to "wireless", // 🛜
        0x1F6DD to "playground slide", // 🛝
        0x1F6DE to "wheel", // 🛞
        0x1F6DF to "ring buoy", // 🛟
        0x1F6E0 to "hammer and wrench", // 🛠
        0x1F6E1 to "shield", // 🛡
        0x1F6E2 to "oil drum", // 🛢
        0x1F6E3 to "motorway", // 🛣
        0x1F6E4 to "railway track", // 🛤
        0x1F6E5 to "motor boat", // 🛥
        0x1F6E9 to "small airplane", // 🛩
        0x1F6EB to "airplane departure", // 🛫
        0x1F6EC to "airplane arrival", // 🛬
        0x1F6F0 to "satellite", // 🛰
        0x1F6F3 to "passenger ship", // 🛳
        0x1F6F4 to "kick scooter", // 🛴
        0x1F6F5 to "motor scooter", // 🛵
        0x1F6F6 to "canoe", // 🛶
        0x1F6F7 to "sled", // 🛷
        0x1F6F8 to "flying saucer", // 🛸
        0x1F6F9 to "skateboard", // 🛹
        0x1F6FA to "auto rickshaw", // 🛺
        0x1F6FB to "pickup truck", // 🛻
        0x1F6FC to "roller skate", // 🛼
        0x1F7E0 to "orange circle", // 🟠
        0x1F7E1 to "yellow circle", // 🟡
        0x1F7E2 to "green circle", // 🟢
        0x1F7E3 to "purple circle", // 🟣
        0x1F7E4 to "brown circle", // 🟤
        0x1F7E5 to "red square", // 🟥
        0x1F7E6 to "blue square", // 🟦
        0x1F7E7 to "orange square", // 🟧
        0x1F7E8 to "yellow square", // 🟨
        0x1F7E9 to "green square", // 🟩
        0x1F7EA to "purple square", // 🟪
        0x1F7EB to "brown square", // 🟫
        0x1F7F0 to "heavy equals sign", // 🟰
        0x1F90C to "pinched fingers", // 🤌
        0x1F90D to "white heart", // 🤍
        0x1F90E to "brown heart", // 🤎
        0x1F90F to "pinching hand", // 🤏
        0x1F910 to "zipper-mouth face", // 🤐
        0x1F911 to "money-mouth face", // 🤑
        0x1F912 to "face with thermometer", // 🤒
        0x1F913 to "nerd face", // 🤓
        0x1F914 to "thinking face", // 🤔
        0x1F915 to "face with head-bandage", // 🤕
        0x1F916 to "robot", // 🤖
        0x1F917 to "smiling face with open hands", // 🤗
        0x1F918 to "sign of the horns", // 🤘
        0x1F919 to "call me hand", // 🤙
        0x1F91A to "raised back of hand", // 🤚
        0x1F91B to "left-facing fist", // 🤛
        0x1F91C to "right-facing fist", // 🤜
        0x1F91D to "handshake", // 🤝
        0x1F91E to "crossed fingers", // 🤞
        0x1F91F to "love-you gesture", // 🤟
        0x1F920 to "cowboy hat face", // 🤠
        0x1F921 to "clown face", // 🤡
        0x1F922 to "nauseated face", // 🤢
        0x1F923 to "rolling on the floor laughing", // 🤣
        0x1F924 to "drooling face", // 🤤
        0x1F925 to "lying face", // 🤥
        0x1F926 to "person facepalming", // 🤦
        0x1F927 to "sneezing face", // 🤧
        0x1F928 to "face with raised eyebrow", // 🤨
        0x1F929 to "star-struck", // 🤩
        0x1F92A to "zany face", // 🤪
        0x1F92B to "shushing face", // 🤫
        0x1F92C to "face with symbols on mouth", // 🤬
        0x1F92D to "face with hand over mouth", // 🤭
        0x1F92E to "face vomiting", // 🤮
        0x1F92F to "exploding head", // 🤯
        0x1F930 to "pregnant woman", // 🤰
        0x1F931 to "breast-feeding", // 🤱
        0x1F932 to "palms up together", // 🤲
        0x1F933 to "selfie", // 🤳
        0x1F934 to "prince", // 🤴
        0x1F935 to "person in tuxedo", // 🤵
        0x1F936 to "Mrs. Claus", // 🤶
        0x1F937 to "person shrugging", // 🤷
        0x1F938 to "person cartwheeling", // 🤸
        0x1F939 to "person juggling", // 🤹
        0x1F93A to "person fencing", // 🤺
        0x1F93C to "people wrestling", // 🤼
        0x1F93D to "person playing water polo", // 🤽
        0x1F93E to "person playing handball", // 🤾
        0x1F93F to "diving mask", // 🤿
        0x1F940 to "wilted flower", // 🥀
        0x1F941 to "drum", // 🥁
        0x1F942 to "clinking glasses", // 🥂
        0x1F943 to "tumbler glass", // 🥃
        0x1F944 to "spoon", // 🥄
        0x1F945 to "goal net", // 🥅
        0x1F947 to "1st place medal", // 🥇
        0x1F948 to "2nd place medal", // 🥈
        0x1F949 to "3rd place medal", // 🥉
        0x1F94A to "boxing glove", // 🥊
        0x1F94B to "martial arts uniform", // 🥋
        0x1F94C to "curling stone", // 🥌
        0x1F94D to "lacrosse", // 🥍
        0x1F94E to "softball", // 🥎
        0x1F94F to "flying disc", // 🥏
        0x1F950 to "croissant", // 🥐
        0x1F951 to "avocado", // 🥑
        0x1F952 to "cucumber", // 🥒
        0x1F953 to "bacon", // 🥓
        0x1F954 to "potato", // 🥔
        0x1F955 to "carrot", // 🥕
        0x1F956 to "baguette bread", // 🥖
        0x1F957 to "green salad", // 🥗
        0x1F958 to "shallow pan of food", // 🥘
        0x1F959 to "stuffed flatbread", // 🥙
        0x1F95A to "egg", // 🥚
        0x1F95B to "glass of milk", // 🥛
        0x1F95C to "peanuts", // 🥜
        0x1F95D to "kiwi fruit", // 🥝
        0x1F95E to "pancakes", // 🥞
        0x1F95F to "dumpling", // 🥟
        0x1F960 to "fortune cookie", // 🥠
        0x1F961 to "takeout box", // 🥡
        0x1F962 to "chopsticks", // 🥢
        0x1F963 to "bowl with spoon", // 🥣
        0x1F964 to "cup with straw", // 🥤
        0x1F965 to "coconut", // 🥥
        0x1F966 to "broccoli", // 🥦
        0x1F967 to "pie", // 🥧
        0x1F968 to "pretzel", // 🥨
        0x1F969 to "cut of meat", // 🥩
        0x1F96A to "sandwich", // 🥪
        0x1F96B to "canned food", // 🥫
        0x1F96C to "leafy green", // 🥬
        0x1F96D to "mango", // 🥭
        0x1F96E to "moon cake", // 🥮
        0x1F96F to "bagel", // 🥯
        0x1F970 to "smiling face with hearts", // 🥰
        0x1F971 to "yawning face", // 🥱
        0x1F972 to "smiling face with tear", // 🥲
        0x1F973 to "partying face", // 🥳
        0x1F974 to "woozy face", // 🥴
        0x1F975 to "hot face", // 🥵
        0x1F976 to "cold face", // 🥶
        0x1F977 to "ninja", // 🥷
        0x1F978 to "disguised face", // 🥸
        0x1F979 to "face holding back tears", // 🥹
        0x1F97A to "pleading face", // 🥺
        0x1F97B to "sari", // 🥻
        0x1F97C to "lab coat", // 🥼
        0x1F97D to "goggles", // 🥽
        0x1F97E to "hiking boot", // 🥾
        0x1F97F to "flat shoe", // 🥿
        0x1F980 to "crab", // 🦀
        0x1F981 to "lion", // 🦁
        0x1F982 to "scorpion", // 🦂
        0x1F983 to "turkey", // 🦃
        0x1F984 to "unicorn", // 🦄
        0x1F985 to "eagle", // 🦅
        0x1F986 to "duck", // 🦆
        0x1F987 to "bat", // 🦇
        0x1F988 to "shark", // 🦈
        0x1F989 to "owl", // 🦉
        0x1F98A to "fox", // 🦊
        0x1F98B to "butterfly", // 🦋
        0x1F98C to "deer", // 🦌
        0x1F98D to "gorilla", // 🦍
        0x1F98E to "lizard", // 🦎
        0x1F98F to "rhinoceros", // 🦏
        0x1F990 to "shrimp", // 🦐
        0x1F991 to "squid", // 🦑
        0x1F992 to "giraffe", // 🦒
        0x1F993 to "zebra", // 🦓
        0x1F994 to "hedgehog", // 🦔
        0x1F995 to "sauropod", // 🦕
        0x1F996 to "T-Rex", // 🦖
        0x1F997 to "cricket", // 🦗
        0x1F998 to "kangaroo", // 🦘
        0x1F999 to "llama", // 🦙
        0x1F99A to "peacock", // 🦚
        0x1F99B to "hippopotamus", // 🦛
        0x1F99C to "parrot", // 🦜
        0x1F99D to "raccoon", // 🦝
        0x1F99E to "lobster", // 🦞
        0x1F99F to "mosquito", // 🦟
        0x1F9A0 to "microbe", // 🦠
        0x1F9A1 to "badger", // 🦡
        0x1F9A2 to "swan", // 🦢
        0x1F9A3 to "mammoth", // 🦣
        0x1F9A4 to "dodo", // 🦤
        0x1F9A5 to "sloth", // 🦥
        0x1F9A6 to "otter", // 🦦
        0x1F9A7 to "orangutan", // 🦧
        0x1F9A8 to "skunk", // 🦨
        0x1F9A9 to "flamingo", // 🦩
        0x1F9AA to "oyster", // 🦪
        0x1F9AB to "beaver", // 🦫
        0x1F9AC to "bison", // 🦬
        0x1F9AD to "seal", // 🦭
        0x1F9AE to "guide dog", // 🦮
        0x1F9AF to "white cane", // 🦯
        0x1F9B4 to "bone", // 🦴
        0x1F9B5 to "leg", // 🦵
        0x1F9B6 to "foot", // 🦶
        0x1F9B7 to "tooth", // 🦷
        0x1F9B8 to "superhero", // 🦸
        0x1F9B9 to "supervillain", // 🦹
        0x1F9BA to "safety vest", // 🦺
        0x1F9BB to "ear with hearing aid", // 🦻
        0x1F9BC to "motorized wheelchair", // 🦼
        0x1F9BD to "manual wheelchair", // 🦽
        0x1F9BE to "mechanical arm", // 🦾
        0x1F9BF to "mechanical leg", // 🦿
        0x1F9C0 to "cheese wedge", // 🧀
        0x1F9C1 to "cupcake", // 🧁
        0x1F9C2 to "salt", // 🧂
        0x1F9C3 to "beverage box", // 🧃
        0x1F9C4 to "garlic", // 🧄
        0x1F9C5 to "onion", // 🧅
        0x1F9C6 to "falafel", // 🧆
        0x1F9C7 to "waffle", // 🧇
        0x1F9C8 to "butter", // 🧈
        0x1F9C9 to "mate", // 🧉
        0x1F9CA to "ice", // 🧊
        0x1F9CB to "bubble tea", // 🧋
        0x1F9CC to "troll", // 🧌
        0x1F9CD to "person standing", // 🧍
        0x1F9CE to "person kneeling", // 🧎
        0x1F9CF to "deaf person", // 🧏
        0x1F9D0 to "face with monocle", // 🧐
        0x1F9D1 to "person", // 🧑
        0x1F9D2 to "child", // 🧒
        0x1F9D3 to "older person", // 🧓
        0x1F9D4 to "person: beard", // 🧔
        0x1F9D5 to "woman with headscarf", // 🧕
        0x1F9D6 to "person in steamy room", // 🧖
        0x1F9D7 to "person climbing", // 🧗
        0x1F9D8 to "person in lotus position", // 🧘
        0x1F9D9 to "mage", // 🧙
        0x1F9DA to "fairy", // 🧚
        0x1F9DB to "vampire", // 🧛
        0x1F9DC to "merperson", // 🧜
        0x1F9DD to "elf", // 🧝
        0x1F9DE to "genie", // 🧞
        0x1F9DF to "zombie", // 🧟
        0x1F9E0 to "brain", // 🧠
        0x1F9E1 to "orange heart", // 🧡
        0x1F9E2 to "billed cap", // 🧢
        0x1F9E3 to "scarf", // 🧣
        0x1F9E4 to "gloves", // 🧤
        0x1F9E5 to "coat", // 🧥
        0x1F9E6 to "socks", // 🧦
        0x1F9E7 to "red envelope", // 🧧
        0x1F9E8 to "firecracker", // 🧨
        0x1F9E9 to "puzzle piece", // 🧩
        0x1F9EA to "test tube", // 🧪
        0x1F9EB to "petri dish", // 🧫
        0x1F9EC to "dna", // 🧬
        0x1F9ED to "compass", // 🧭
        0x1F9EE to "abacus", // 🧮
        0x1F9EF to "fire extinguisher", // 🧯
        0x1F9F0 to "toolbox", // 🧰
        0x1F9F1 to "brick", // 🧱
        0x1F9F2 to "magnet", // 🧲
        0x1F9F3 to "luggage", // 🧳
        0x1F9F4 to "lotion bottle", // 🧴
        0x1F9F5 to "thread", // 🧵
        0x1F9F6 to "yarn", // 🧶
        0x1F9F7 to "safety pin", // 🧷
        0x1F9F8 to "teddy bear", // 🧸
        0x1F9F9 to "broom", // 🧹
        0x1F9FA to "basket", // 🧺
        0x1F9FB to "roll of paper", // 🧻
        0x1F9FC to "soap", // 🧼
        0x1F9FD to "sponge", // 🧽
        0x1F9FE to "receipt", // 🧾
        0x1F9FF to "nazar amulet", // 🧿
        0x1FA70 to "ballet shoes", // 🩰
        0x1FA71 to "one-piece swimsuit", // 🩱
        0x1FA72 to "briefs", // 🩲
        0x1FA73 to "shorts", // 🩳
        0x1FA74 to "thong sandal", // 🩴
        0x1FA75 to "light blue heart", // 🩵
        0x1FA76 to "grey heart", // 🩶
        0x1FA77 to "pink heart", // 🩷
        0x1FA78 to "drop of blood", // 🩸
        0x1FA79 to "adhesive bandage", // 🩹
        0x1FA7A to "stethoscope", // 🩺
        0x1FA7B to "x-ray", // 🩻
        0x1FA7C to "crutch", // 🩼
        0x1FA80 to "yo-yo", // 🪀
        0x1FA81 to "kite", // 🪁
        0x1FA82 to "parachute", // 🪂
        0x1FA83 to "boomerang", // 🪃
        0x1FA84 to "magic wand", // 🪄
        0x1FA85 to "pinata", // 🪅
        0x1FA86 to "nesting dolls", // 🪆
        0x1FA87 to "maracas", // 🪇
        0x1FA88 to "flute", // 🪈
        0x1FA89 to "harp", // 🪉
        0x1FA8F to "shovel", // 🪏
        0x1FA90 to "ringed planet", // 🪐
        0x1FA91 to "chair", // 🪑
        0x1FA92 to "razor", // 🪒
        0x1FA93 to "axe", // 🪓
        0x1FA94 to "diya lamp", // 🪔
        0x1FA95 to "banjo", // 🪕
        0x1FA96 to "military helmet", // 🪖
        0x1FA97 to "accordion", // 🪗
        0x1FA98 to "long drum", // 🪘
        0x1FA99 to "coin", // 🪙
        0x1FA9A to "carpentry saw", // 🪚
        0x1FA9B to "screwdriver", // 🪛
        0x1FA9C to "ladder", // 🪜
        0x1FA9D to "hook", // 🪝
        0x1FA9E to "mirror", // 🪞
        0x1FA9F to "window", // 🪟
        0x1FAA0 to "plunger", // 🪠
        0x1FAA1 to "sewing needle", // 🪡
        0x1FAA2 to "knot", // 🪢
        0x1FAA3 to "bucket", // 🪣
        0x1FAA4 to "mouse trap", // 🪤
        0x1FAA5 to "toothbrush", // 🪥
        0x1FAA6 to "headstone", // 🪦
        0x1FAA7 to "placard", // 🪧
        0x1FAA8 to "rock", // 🪨
        0x1FAA9 to "mirror ball", // 🪩
        0x1FAAA to "identification card", // 🪪
        0x1FAAB to "low battery", // 🪫
        0x1FAAC to "hamsa", // 🪬
        0x1FAAD to "folding hand fan", // 🪭
        0x1FAAE to "hair pick", // 🪮
        0x1FAAF to "khanda", // 🪯
        0x1FAB0 to "fly", // 🪰
        0x1FAB1 to "worm", // 🪱
        0x1FAB2 to "beetle", // 🪲
        0x1FAB3 to "cockroach", // 🪳
        0x1FAB4 to "potted plant", // 🪴
        0x1FAB5 to "wood", // 🪵
        0x1FAB6 to "feather", // 🪶
        0x1FAB7 to "lotus", // 🪷
        0x1FAB8 to "coral", // 🪸
        0x1FAB9 to "empty nest", // 🪹
        0x1FABA to "nest with eggs", // 🪺
        0x1FABB to "hyacinth", // 🪻
        0x1FABC to "jellyfish", // 🪼
        0x1FABD to "wing", // 🪽
        0x1FABE to "leafless tree", // 🪾
        0x1FABF to "goose", // 🪿
        0x1FAC0 to "anatomical heart", // 🫀
        0x1FAC1 to "lungs", // 🫁
        0x1FAC2 to "people hugging", // 🫂
        0x1FAC3 to "pregnant man", // 🫃
        0x1FAC4 to "pregnant person", // 🫄
        0x1FAC5 to "person with crown", // 🫅
        0x1FAC6 to "fingerprint", // 🫆
        0x1FACE to "moose", // 🫎
        0x1FACF to "donkey", // 🫏
        0x1FAD0 to "blueberries", // 🫐
        0x1FAD1 to "bell pepper", // 🫑
        0x1FAD2 to "olive", // 🫒
        0x1FAD3 to "flatbread", // 🫓
        0x1FAD4 to "tamale", // 🫔
        0x1FAD5 to "fondue", // 🫕
        0x1FAD6 to "teapot", // 🫖
        0x1FAD7 to "pouring liquid", // 🫗
        0x1FAD8 to "beans", // 🫘
        0x1FAD9 to "jar", // 🫙
        0x1FADA to "ginger root", // 🫚
        0x1FADB to "pea pod", // 🫛
        0x1FADC to "root vegetable", // 🫜
        0x1FADF to "splatter", // 🫟
        0x1FAE0 to "melting face", // 🫠
        0x1FAE1 to "saluting face", // 🫡
        0x1FAE2 to "face with open eyes and hand over mouth", // 🫢
        0x1FAE3 to "face with peeking eye", // 🫣
        0x1FAE4 to "face with diagonal mouth", // 🫤
        0x1FAE5 to "dotted line face", // 🫥
        0x1FAE6 to "biting lip", // 🫦
        0x1FAE7 to "bubbles", // 🫧
        0x1FAE8 to "shaking face", // 🫨
        0x1FAE9 to "face with bags under eyes", // 🫩
        0x1FAF0 to "hand with index finger and thumb crossed", // 🫰
        0x1FAF1 to "rightwards hand", // 🫱
        0x1FAF2 to "leftwards hand", // 🫲
        0x1FAF3 to "palm down hand", // 🫳
        0x1FAF4 to "palm up hand", // 🫴
        0x1FAF5 to "index pointing at the viewer", // 🫵
        0x1FAF6 to "heart hands", // 🫶
        0x1FAF7 to "leftwards pushing hand", // 🫷
        0x1FAF8 to "rightwards pushing hand", // 🫸
    )
}
