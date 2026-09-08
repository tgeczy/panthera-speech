"""Every position of the phrase-breaks setting must do something different.

It shipped with five positions of which two -- "fewer" at -2.0 and "more" at
+2.0 -- rendered **byte-identical** output, because `Boundaries.SilThreshold`
has a dead zone: measured, every value from -3 to +3 behaves the same, and on a
short phrase every value from -20 to +8 does.  Tomi heard it as "most doesn't
bring it back up like Leopard original does".

Two values are the same behaviour only if they are identical on *every* text,
which leaves six numeric classes to pick from; the five positions are four of
those plus the unanswered mode, and all five are byte-distinct.
"""
import pytest

from synthDrivers import leopardspeech


#: Long enough to have phrasing decisions in it.  The short-phrase case cannot
#: be used here: on "Restart with debug logging enabled" every number in range
#: is byte-identical, and only Leopard's own differs.
TEXT = ("The US Chamber of Commerce had also warned Tuesday that higher "
        "tariffs would damage both economies, drive up costs for families, "
        "further disrupt critical supply chains, and risk the 13 million "
        "American jobs that depend on trade. Negotiators met again on "
        "Wednesday morning, but neither side would say whether a deal was "
        "close, and the deadline is now only days away.")


def test_every_position_renders_differently(driver):
    """The bug this file exists for: two positions that were the same."""
    renders = {}
    for name in leopardspeech.SynthDriver.PHRASING:
        driver._set_phrasing(name)
        renders[name] = driver._render(TEXT, driver._wpm(),
                                       driver._get_voice())
        assert renders[name], "%s rendered nothing" % name

    seen = {}
    for name, audio in renders.items():
        key = hash(audio)
        if key in seen:
            pytest.fail("%r and %r render identically -- two positions of the "
                        "setting do the same thing" % (seen[key], name))
        seen[key] = name


def test_leopards_own_is_the_one_that_is_unanswered(driver):
    """It is a different model, not a lower threshold.

    Everything else answers the parameter; this position deliberately does not,
    and that is why it cannot be placed on the numeric scale honestly.
    """
    driver._set_phrasing("leopard")
    assert driver._phrasingParam() is None
    for name in ("fewest", "more", "most"):
        driver._set_phrasing(name)
        assert driver._phrasingParam().startswith("Boundaries.SilThreshold=")


def test_every_position_survived_the_rebuild(driver):
    """All five names still resolve, so no saved choice silently resets.

    "fewer" kept its name and moved from -2.0, where it duplicated "more", to
    -4.0, where it is its own class.  Losing the name would have reset anyone
    who had chosen it.
    """
    for name in ("fewest", "fewer", "more", "most", "leopard"):
        driver._set_phrasing(name)
        assert driver._get_phrasing() == name


def test_an_unknown_position_falls_back(driver):
    driver._set_phrasing("nonsense")
    assert driver._get_phrasing() == "fewest"
