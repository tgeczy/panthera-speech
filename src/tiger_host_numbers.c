/* Numbers the engine reads wrongly, rewritten before it sees them.
 *
 * Ported from `pantheranumbers.py`, which is the reference and stays the
 * reference: `--numbers-check` diffs this against it, case for case.
 *
 * **Why it is here and not in each front end.**  This rewriting began in the
 * NVDA driver, in Python.  The SAPI bridge then needed it and got a partial
 * reimplementation in C++ -- `fix_long_numbers`, which groups seven-digit runs
 * and does nothing about versions, leading zeros, the sign, or the "words"
 * style.  Android got nothing at all, so on a phone every number from seven
 * digits up is still spelled one digit at a time.
 *
 * Two implementations is already one too many, and they do not agree: given
 * `1234567KB`, Python deliberately leaves it alone -- the engine's own
 * dictionary has rules for a number glued to a unit, and rewriting it takes
 * the rule away without replacing it -- while the SAPI copy's digit-run
 * scanner rewrites it to `1,234,567KB`.  Same input, different output,
 * depending which product the user happens to be in.  So it lives in the host
 * now, where every front end reaches the same code.
 *
 * **Placement was measured, not assumed.**  Python and SAPI both run numbers
 * BEFORE abbreviations; the host necessarily runs after everything the front
 * end did.  Running both orders over 183 inputs from the two test suites, plus
 * crossings written to break it ("Dr. 1234567 Main St.", "1234567mm"), gives
 * byte-identical output in both styles -- the two stages touch disjoint token
 * classes, so they commute.
 *
 * The engine's behaviour this repairs, measured on Alex:
 *
 *     1234        "twelve thirty four"        -- read as a year
 *     123456      "one hundred twenty three thousand four hundred 'n' fifty six"
 *     1234567     "1 2 3 4 5 6 7"             <- spelled, one digit at a time
 *
 * Seven digits is where it stops, and the fix the engine itself suggests is
 * already in the text: `1,234,567` with its separators is read correctly.  So
 * group the digits and let the engine go on doing its own arithmetic in its
 * own style, rather than replacing its number reading with ours.
 */

#define NUM_STYLE_OFF   0
#define NUM_STYLE_FIX   1
#define NUM_STYLE_WORDS 2

/* Above this many digits the engine spells them out one at a time. */
#define NUM_LONG_DIGITS 7

static const char *const NUM_ONES[20] = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen"
};
static const char *const NUM_TENS[10] = {
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety"
};
/* Stops at trillion deliberately: past that the words stop being agreed on,
 * and a number that long is an identifier rather than a quantity anyway. */
static const struct { unsigned long long value; const char *name; }
NUM_SCALES[4] = {
    { 1000000000000ULL, "trillion" }, { 1000000000ULL, "billion" },
    { 1000000ULL, "million" },        { 1000ULL, "thousand" }
};

/* A growing output buffer, so a rewrite can be any length without any call
 * site having to guess one.  `cap` of 0 means the append is being measured
 * rather than performed. */
typedef struct { char *buf; size_t len, cap; int failed; } numbuf;

static void nb_add(numbuf *b, const char *s, size_t n)
{
    if (b->failed) return;
    if (b->len + n + 1 > b->cap) {
        size_t want = (b->len + n + 1) * 2;
        char *p = (char *)realloc(b->buf, want);
        if (!p) { b->failed = 1; return; }
        b->buf = p;
        b->cap = want;
    }
    memcpy(b->buf + b->len, s, n);
    b->len += n;
    b->buf[b->len] = 0;
}
static void nb_str(numbuf *b, const char *s) { nb_add(b, s, strlen(s)); }

/* An unsigned integer in English words, or 0 if it is too large to say --
 * better left as digits than given a name nobody agrees on. */
static int num_to_words(numbuf *b, unsigned long long n)
{
    int i;
    if (n < 20) { nb_str(b, NUM_ONES[n]); return 1; }
    if (n < 100) {
        nb_str(b, NUM_TENS[n / 10]);
        if (n % 10) { nb_str(b, " "); nb_str(b, NUM_ONES[n % 10]); }
        return 1;
    }
    if (n < 1000) {
        nb_str(b, NUM_ONES[n / 100]);
        nb_str(b, " hundred");
        if (n % 100) { nb_str(b, " "); return num_to_words(b, n % 100); }
        return 1;
    }
    if (n >= 1000ULL * NUM_SCALES[0].value) return 0;
    /* Largest scale first, and the test is `n >= value`, not
     * `n < value * 1000`: the latter picks "trillion" for one thousand, names
     * it "zero trillion", and recurses on the same number for ever. */
    for (i = 0; i < 4; i++) {
        if (n < NUM_SCALES[i].value) continue;
        if (!num_to_words(b, n / NUM_SCALES[i].value)) return 0;
        nb_str(b, " ");
        nb_str(b, NUM_SCALES[i].name);
        if (n % NUM_SCALES[i].value) {
            nb_str(b, " ");
            return num_to_words(b, n % NUM_SCALES[i].value);
        }
        return 1;
    }
    return 0;
}

/* Does this run of digits fit an unsigned long long?  Twenty digits is where
 * it stops mattering: `num_to_words` refuses anything past a thousand trillion
 * anyway, and a longer run is an account number, not a quantity. */
static int num_parse(const char *s, size_t n, unsigned long long *out)
{
    unsigned long long v = 0;
    size_t i;
    if (n == 0 || n > 19) return 0;
    for (i = 0; i < n; i++) v = v * 10 + (unsigned)(s[i] - '0');
    *out = v;
    return 1;
}

/* "seven five" for "75" -- how the part after a point is read. */
static void num_digits_aloud(numbuf *b, const char *s, size_t n)
{
    size_t i;
    for (i = 0; i < n; i++) {
        if (i) nb_str(b, " ");
        nb_str(b, NUM_ONES[s[i] - '0']);
    }
}

/* "3,222,233" for "3222233", which the engine reads correctly. */
static void num_group(numbuf *b, const char *s, size_t n)
{
    size_t head = n % 3, i;
    if (head == 0) head = 3;
    nb_add(b, s, head);
    for (i = head; i < n; i += 3) {
        nb_str(b, ",");
        nb_add(b, s + i, 3);
    }
}

static int num_is_alnum(unsigned char c)
{
    return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') ||
           (c >= 'a' && c <= 'z');
}
static int num_is_digit(unsigned char c) { return c >= '0' && c <= '9'; }

/* Is this exactly a match of `-?\d[\d,]*(?:\.\d+)*`?
 *
 * Backtracking needs it: shortening the greedy match can land on something
 * the pattern could never have produced (a token ending mid-dot-group, say),
 * and the regex would never offer that as a candidate either. */
static int num_token_valid(const char *s, size_t len)
{
    size_t i = 0;
    if (len == 0) return 0;
    if (s[0] == '-') i++;
    if (i >= len || !num_is_digit((unsigned char)s[i])) return 0;
    i++;
    while (i < len && (num_is_digit((unsigned char)s[i]) || s[i] == ',')) i++;
    while (i < len) {
        if (s[i] != '.') return 0;
        i++;
        if (i >= len || !num_is_digit((unsigned char)s[i])) return 0;
        while (i < len && num_is_digit((unsigned char)s[i])) i++;
    }
    return 1;
}

/* One matched token, rewritten into `b`.  `tok`/`n` is the token exactly as it
 * appeared, sign included.  Returns 0 to mean "leave it alone", in which case
 * nothing has been appended. */
static int num_rewrite(numbuf *b, const char *tok, size_t n, int style)
{
    const char *body = tok;
    size_t blen = n, i, start;
    int negative = 0, nparts = 0;
    struct { const char *p; size_t n; } part[8];
    char clean[64];
    size_t cn = 0;

    if (blen && body[0] == '-') { negative = 1; body++; blen--; }

    /* Commas are the caller's; strip them, then split on the dots.  Anything
     * longer than this buffer is not a quantity worth rewriting. */
    for (i = 0; i < blen; i++) {
        if (body[i] == ',') continue;
        if (cn + 1 >= sizeof clean) return 0;
        clean[cn++] = body[i];
    }
    clean[cn] = 0;

    start = 0;
    for (i = 0; i <= cn; i++) {
        if (i != cn && clean[i] != '.') continue;
        if (nparts >= 8) return 0;
        /* An empty part means a trailing or doubled dot: leave it alone,
         * exactly as `if not all(parts)` does. */
        if (i == start) return 0;
        part[nparts].p = clean + start;
        part[nparts].n = i - start;
        nparts++;
        start = i + 1;
    }
    if (nparts == 0) return 0;

    /* Three or more parts is a version, never a quantity: 0.7.3.  Each part is
     * its own number and the separators have to be spoken, which is the one
     * case the engine loses entirely. */
    if (nparts > 2) {
        size_t mark = b->len;
        if (negative) nb_str(b, "minus ");
        for (i = 0; i < (size_t)nparts; i++) {
            unsigned long long v;
            if (i) nb_str(b, " point ");
            if (!num_parse(part[i].p, part[i].n, &v) || !num_to_words(b, v)) {
                b->len = mark;              /* unsay it and keep the digits */
                if (b->buf) b->buf[mark] = 0;
                return 0;
            }
        }
        return 1;
    }

    {
        const char *whole = part[0].p, *frac = NULL;
        size_t wn = part[0].n, fn = 0;
        if (nparts == 2) { frac = part[1].p; fn = part[1].n; }

        if (style == NUM_STYLE_WORDS) {
            unsigned long long v;
            size_t mark = b->len;
            if (negative) nb_str(b, "minus ");
            if (!num_parse(whole, wn, &v) || !num_to_words(b, v)) {
                b->len = mark;
                if (b->buf) b->buf[mark] = 0;
                return 0;
            }
            if (frac) { nb_str(b, " point "); num_digits_aloud(b, frac, fn); }
            return 1;
        }

        /* NUM_STYLE_FIX: change only what the engine gets wrong, and leave its
         * own number reading alone everywhere else. */
        if (frac) {
            /* A leading zero is dropped by the engine -- "0.5" is heard as
             * "point five" -- so that one is written out.  Every other decimal
             * is left as it is, because the engine reads those correctly. */
            int allzero = 1;
            for (i = 0; i < wn; i++) if (whole[i] != '0') { allzero = 0; break; }
            if (!allzero || wn == 0 || wn > 2) return 0;
            if (negative) nb_str(b, "minus ");
            nb_str(b, "zero point ");
            num_digits_aloud(b, frac, fn);
            return 1;
        }
        if (wn >= NUM_LONG_DIGITS) {
            /* Grouped, not spelled: the engine reads "1,234,567" correctly, so
             * it keeps its own phrasing and we change as little as possible. */
            if (negative) nb_str(b, "-");
            num_group(b, whole, wn);
            return 1;
        }
        return 0;
    }
}

/* The token scanner, standing in for
 *
 *     (?<![A-Za-z0-9,.])(-?\d[\d,]*(?:\.\d+)*)(?![A-Za-z0-9]|\.\d)
 *
 * Both lookarounds earn their place.  The leading one keeps `MP3` intact, the
 * trailing one keeps `5KB` -- those belong to the engine's dictionary, which
 * has rules for them.  And `\.\d` in the trailing guard is not decoration:
 * without it "1.5x" is not left alone, because the decimal part cannot be
 * taken (the "x" fails the guard), so the match backtracks to the bare "1",
 * and the "." after it is not a letter, so the guard passes and "1.5x" comes
 * out as "one.5x".
 *
 * `(?:\.\d+)*` repeats, and that repetition is the whole of version support:
 * it is what lets "0.7.3" match as one token rather than as "0.7" and a
 * stray ".3".
 */
static char *num_expand(const char *text, int style)
{
    numbuf b;
    size_t i = 0, n;
    if (!text) return NULL;
    n = strlen(text);
    memset(&b, 0, sizeof b);
    nb_add(&b, "", 0);                       /* an empty string, not NULL */
    if (b.failed) return NULL;

    if (style == NUM_STYLE_OFF) { nb_add(&b, text, n); return b.failed ? NULL : b.buf; }

    while (i < n) {
        size_t start = i, digits, j;
        unsigned char prev;

        /* Never inside an embedded command.  With commands accepted,
         * "[[rate 200]]" is still in the text here, and rewriting the 200
         * inside it would leave the engine reading "[[rate two hundred]]" as
         * something unparseable.  The Python driver gets this by splitting on
         * commands before it calls in; the host has to do it itself. */
        if (text[i] == '[' && i + 1 < n && text[i + 1] == '[') {
            const char *close = strstr(text + i + 2, "]]");
            if (close && (size_t)(close - (text + i + 2)) <= 64) {
                size_t len = (size_t)(close + 2 - (text + i));
                nb_add(&b, text + i, len);
                i += len;
                continue;
            }
        }

        if (!num_is_digit((unsigned char)text[i]) &&
            !(text[i] == '-' && i + 1 < n && num_is_digit((unsigned char)text[i + 1]))) {
            nb_add(&b, text + i, 1);
            i++;
            continue;
        }

        /* The lookbehind: no letter, digit, comma or dot immediately before. */
        prev = start ? (unsigned char)text[start - 1] : 0;
        if (start && (num_is_alnum(prev) || prev == ',' || prev == '.')) {
            nb_add(&b, text + i, 1);
            i++;
            continue;
        }

        /* Longest match first, then give characters back until the lookahead
         * is satisfied -- which is what the regex engine does, and it is
         * observable rather than academic.  Two cases in the corpus depend
         * on it, and the reference's answer to the second is not one anybody
         * would choose:
         *
         *   "1234567,"          the trailing comma is INSIDE the token
         *                       (`[\d,]*` takes it, the lookahead at end of
         *                       string is happy) and is swallowed by the
         *                       rewrite, so the comma does not come back.
         *
         *   "the 1,234,567th"   the full number fails the lookahead on "t",
         *                       and backtracking settles on "1,234" -- whose
         *                       own lookahead sees a comma and passes.  The
         *                       reference therefore says "one thousand two
         *                       hundred thirty four,567th".
         *
         * That second one is a bug, and it is the REFERENCE'S bug.  Matching
         * it is the point: one behaviour on every platform, and one place to
         * fix it when it is fixed.  A port that quietly improved on its
         * reference would be the expensive kind of disagreement -- two halves
         * that differ only in cases nobody tests. */
        {
            size_t jmax, jmin, k;
            int matched = 0;
            jmin = (text[i] == '-') ? i + 2 : i + 1;      /* `-?\d` at least */
            jmax = jmin;
            k = jmin;
            while (k < n && (num_is_digit((unsigned char)text[k]) || text[k] == ',')) k++;
            jmax = k;
            for (;;) {                                    /* `(?:\.\d+)*` */
                size_t g = jmax;
                if (g < n && text[g] == '.' && g + 1 < n &&
                    num_is_digit((unsigned char)text[g + 1])) {
                    g++;
                    while (g < n && num_is_digit((unsigned char)text[g])) g++;
                    jmax = g;
                    continue;
                }
                break;
            }
            for (j = jmax; j >= jmin; j--) {
                if (!num_token_valid(text + i, j - i)) continue;
                /* The lookahead: not a letter or digit, and not a dot then a
                 * digit -- the `\.\d` half is what keeps "1.5x" whole. */
                if (j < n && (num_is_alnum((unsigned char)text[j]) ||
                              (text[j] == '.' && j + 1 < n &&
                               num_is_digit((unsigned char)text[j + 1]))))
                    continue;
                matched = 1;
                break;
            }
            if (!matched) {
                nb_add(&b, text + i, 1);     /* not a token; pass the char on */
                i++;
                continue;
            }
        }
        (void)digits;

        if (!num_rewrite(&b, text + i, j - i, style))
            nb_add(&b, text + i, j - i);     /* left alone, exactly as it was */
        i = j;
    }
    return b.failed ? (free(b.buf), (char *)NULL) : b.buf;
}

static int num_style_of(const char *name)
{
    if (!name || !*name) return NUM_STYLE_FIX;
    if (!strcmp(name, "off"))   return NUM_STYLE_OFF;
    if (!strcmp(name, "words")) return NUM_STYLE_WORDS;
    return NUM_STYLE_FIX;
}

/* --------------------------------------------------------------- self test */

/* `tiger_host --numbers-check`, the same shape as --aac-check and --regex-check
 * and for the same reason: a rewriting rule is easy to get subtly wrong and
 * impossible to see being wrong from the outside, where its only trace is a
 * number read oddly.
 *
 * Every case below is lifted from `panthera/tests/leopard/test_numbers.py`,
 * which is the reference implementation's own suite -- not expectations
 * written to match this code.  `tools/numbers_oracle.py` then diffs a much
 * larger generated corpus against the Python module directly, because fifteen
 * assertions is a thin oracle for a hand-rolled tokenizer.
 */
static int num_one(const char *in, int style, const char *want, int *fails)
{
    char *got = num_expand(in, style);
    const char *style_name = style == NUM_STYLE_OFF ? "off"
                           : style == NUM_STYLE_WORDS ? "words" : "fix";
    int ok = got && !strcmp(got, want);
    if (!ok) {
        fprintf(stdout, "FAIL  %-28s [%s]\n        got  \"%s\"\n        want \"%s\"\n",
                in, style_name, got ? got : "(null)", want);
        (*fails)++;
    }
    free(got);
    return ok;
}

static int num_words_one(unsigned long long n, const char *want, int *fails)
{
    numbuf b;
    int ok;
    memset(&b, 0, sizeof b);
    nb_add(&b, "", 0);
    ok = num_to_words(&b, n) && b.buf && !strcmp(b.buf, want);
    if (!ok) {
        fprintf(stdout, "FAIL  to_words(%llu)\n        got  \"%s\"\n        want \"%s\"\n",
                n, b.buf ? b.buf : "(null)", want);
        (*fails)++;
    }
    free(b.buf);
    return ok;
}

static int num_check(void)
{
    int fails = 0;
    size_t i;

    /* -- integers to words ------------------------------------------- */
    num_words_one(0, "zero", &fails);
    num_words_one(7, "seven", &fails);
    num_words_one(13, "thirteen", &fails);
    num_words_one(20, "twenty", &fails);
    num_words_one(21, "twenty one", &fails);
    num_words_one(100, "one hundred", &fails);
    num_words_one(101, "one hundred one", &fails);
    num_words_one(999, "nine hundred ninety nine", &fails);
    num_words_one(1000, "one thousand", &fails);
    num_words_one(1000000, "one million", &fails);
    num_words_one(1234567, "one million two hundred thirty four thousand "
                           "five hundred sixty seven", &fails);
    /* Better left as digits than given a name nobody agrees on. */
    {
        numbuf b;
        memset(&b, 0, sizeof b);
        nb_add(&b, "", 0);
        if (num_to_words(&b, 1000000000000000000ULL)) {
            fprintf(stdout, "FAIL  10^18 should be refused, got \"%s\"\n",
                    b.buf ? b.buf : "");
            fails++;
        }
        free(b.buf);
    }

    /* -- the reported faults ------------------------------------------ */
    /* Seven is where the engine starts spelling. */
    num_one("3222233", NUM_STYLE_FIX, "3,222,233", &fails);
    num_one("32322333", NUM_STYLE_FIX, "32,322,333", &fails);
    num_one("1234567", NUM_STYLE_FIX, "1,234,567", &fails);
    /* Six digits the engine reads correctly, so nothing should touch them. */
    num_one("1234", NUM_STYLE_FIX, "1234", &fails);
    num_one("12345", NUM_STYLE_FIX, "12345", &fails);
    num_one("123456", NUM_STYLE_FIX, "123456", &fails);
    /* "v0.7.3" was heard as "v point seven point three". */
    num_one("0.7.3", NUM_STYLE_FIX, "zero point seven point three", &fails);
    num_one("v0.7.3", NUM_STYLE_FIX, "v0.7.3", &fails);
    num_one("version 0.7.3", NUM_STYLE_FIX,
            "version zero point seven point three", &fails);
    num_one("0.5", NUM_STYLE_FIX, "zero point five", &fails);
    num_one("0.75", NUM_STYLE_FIX, "zero point seven five", &fails);
    /* Other decimals the engine reads correctly; rewriting only risks them. */
    num_one("1.5", NUM_STYLE_FIX, "1.5", &fails);
    num_one("10.7", NUM_STYLE_FIX, "10.7", &fails);
    num_one("3.14", NUM_STYLE_FIX, "3.14", &fails);

    /* -- what must not be touched ------------------------------------- */
    /* 5KB and 1,234MB have engine rules of their own; rewriting them here
     * would take the rule away without replacing it. */
    {
        static const char *const keep[] = { "5KB", "1,234MB", "20ish", "MP3",
                                            "H2O", NULL };
        for (i = 0; keep[i]; i++)
            num_one(keep[i], NUM_STYLE_FIX, keep[i], &fails);
    }
    {
        static const char *const off[] = { "3222233", "0.7.3", "0.5", NULL };
        for (i = 0; off[i]; i++)
            num_one(off[i], NUM_STYLE_OFF, off[i], &fails);
    }

    /* -- words ---------------------------------------------------------- */
    num_one("1234", NUM_STYLE_WORDS, "one thousand two hundred thirty four", &fails);
    num_one("1.5", NUM_STYLE_WORDS, "one point five", &fails);
    num_one("3222233", NUM_STYLE_WORDS,
            "three million two hundred twenty two thousand two hundred "
            "thirty three", &fails);

    /* -- the text around the number survives ---------------------------- */
    num_one("it rose to 3222233 units in 1999", NUM_STYLE_FIX,
            "it rose to 3,222,233 units in 1999", &fails);

    /* -- the host's own addition: never inside an embedded command ------ */
    /* The Python driver splits on commands before it calls in, so it never
     * needs this; the host is handed the text whole and does. */
    num_one("[[rate 2000000]] 3222233", NUM_STYLE_FIX,
            "[[rate 2000000]] 3,222,233", &fails);
    /* MacRoman here, Unicode there -- and the answer is the same, which was
     * worth checking rather than assuming.  The reference guards with
     * `[A-Za-z0-9]`, and an accented letter is no more in that class in
     * Python than 0xE9 is in this one, so BOTH rewrite a number glued to one.
     * Arguably neither should.  That is the reference's call to make and not
     * this port's; what this case pins is that the two agree.  0xE9 is
     * MacRoman 'e' with an acute. */
    num_one("caf\xe9""1234567", NUM_STYLE_FIX, "caf\xe9""1,234,567", &fails);

    fprintf(stdout, "[numbers-check] %d failure(s)\n", fails);
    return fails ? 1 : 0;
}
