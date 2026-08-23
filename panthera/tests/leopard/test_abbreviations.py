"""The dictionary's rules must fire the same way every time.

Reported as "sometimes I have to toggle the checkbox several times to get the
dictionary to fire up... no rhyme or reason as to how many".  The checkbox was
never the variable: the same text, in one host, with nothing touched between,
came back expanded or not depending on the run.

These are the *dictionary* rules -- 5KB, 1,234MB, 20ish -- which are regular
expressions this host compiles, so "Expand abbreviations" turns them off by
declining to compile them.  The engine has a second, separate set that no
setting of its own reaches: `tests/test_acronyms.py`, and DR read as "doctor".
"""


def _say(driver, text):
    return driver._render(text, driver._wpm(), driver._get_voice())


def test_an_abbreviation_expands_the_same_way_every_time(driver):
    """The reproduction, without a setting in sight.

    "the file is 5KB" renders as "five kilobytes" (30800 frames) or as
    "five K B" (27440) depending on what was spoken before it, because the
    framework hands `regexec` a pointer into an *unterminated* word buffer with
    REG_STARTEND and the bounds in pmatch[0].  Reading to the first NUL matched
    the word plus whatever followed it in memory -- and every one of these
    patterns is anchored with '$', so the rubbish decided the answer:

        [re] exec 5KBE            -> MATCH   (the next byte happened to be 'E')
        [re] exec 5KBE<binary>    -> no      (the next run, it did not)

    Speaking "1,234MB" in between is what changed the memory reliably enough to
    reproduce it in three utterances.
    """
    driver._set_expandAbbreviations(True)
    first = _say(driver, "the file is 5KB")
    assert first, "nothing rendered"

    renders = {first}
    for _ in range(12):
        _say(driver, "1,234MB")
        renders.add(_say(driver, "the file is 5KB"))
        _say(driver, "20ish files and chapter III")
        renders.add(_say(driver, "the file is 5KB"))

    assert len(renders) == 1, (
        "the same text rendered %d different ways: the rule fires only "
        "sometimes" % len(renders))


def test_the_rule_is_still_doing_something(driver):
    """A stable render proves nothing if the rule never fires at all.

    Without this, the test above passes just as well when the abbreviation is
    never expanded -- which is the failure it exists to catch.
    """
    driver._set_expandAbbreviations(True)
    expanded = _say(driver, "the file is 5KB")
    driver._set_expandAbbreviations(False)
    plain = _say(driver, "the file is 5KB")
    driver._set_expandAbbreviations(True)

    assert expanded and plain
    assert expanded != plain, "the abbreviation rule did nothing either way"
    # Only that it differs.  Which way is longer is not a safe assertion: on
    # Fred "5KB" grows from 27440 frames to 30800 when it becomes "kilobytes",
    # but "20ish" *shrinks* from 35056 to 21056 when it stops being spelt out,
    # so the direction belongs to the text and the voice, not to the rule.


# -- the whole driver, not just the rule ----------------------------------
#
# `tests/test_acronyms.py` proves the table and the pattern; this proves the
# *setting* reaches them.  The gap is real: the first version of this check
# assigned `driver.expandAbbreviations = False` and every case passed with the
# rewrite never running, because the fake `SynthDriver` has no property
# machinery and the assignment simply made a new attribute.  It is the setter
# that has to be called, and that is what NVDA calls.

def _both_ways(driver, text):
    """-> (audio with the setting on, audio with it off).

    **On Fred, deliberately.**  The fixture's default voice is Alex, and Alex
    is concatenative: "II" and "two" go through the same front end and come out
    as the same words, but not as the same samples, so comparing them proves
    nothing.  A formant voice renders the same phonemes to the same bytes,
    which is what makes "the engine really does expand this" a fact rather than
    an opinion.  Found by writing this on Alex first and watching it fail.
    """
    driver._set_voice("Fred")
    driver._set_expandAbbreviations(True)
    on = _say(driver, text)
    driver._set_expandAbbreviations(False)
    off = _say(driver, text)
    driver._set_expandAbbreviations(True)
    return on, off


def test_turning_it_off_really_stops_dr(driver):
    """DR is a lexical entry in MacinTalk, not a rule out here."""
    on, off = _both_ways(driver, "DR")
    assert on == _say(driver, "doctor"), \
        "the engine no longer expands DR, so this test is about nothing"
    assert off != on, "the setting did not reach the acronym rewrite"


def test_turning_it_off_really_stops_a_roman_numeral(driver):
    """Tomi: "Roman numerals, like XL, are not honored with abbreviations
    off." They were not: the host's list of which dictionary rules the setting
    covers had an entry for them that never matched a pattern, because the
    dictionary has no roman-numeral rule at all."""
    on, off = _both_ways(driver, "II")
    assert on == _say(driver, "two"), \
        "the engine no longer reads II as two, so this test is about nothing"
    assert off != on, "the setting did not reach the roman numerals"
    assert off == _say(driver, "I I"), \
        "II with abbreviations off is not read as its letters"


def test_and_leaves_an_ordinary_word_alone(driver):
    """`MIX` is a valid roman numeral -- M + IX, 1009 -- and an ordinary
    English word. It is the only one, and it stays a word either way."""
    on, off = _both_ways(driver, "MIX")
    assert on == off, "MIX changed when abbreviations were turned off"
