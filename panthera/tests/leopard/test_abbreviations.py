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
