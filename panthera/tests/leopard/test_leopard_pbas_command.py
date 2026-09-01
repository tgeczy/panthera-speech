# -*- coding: utf-8 -*-
"""`[[pbas N]]` in the text moves the voice, when commands are on.

A user reported that Leopard and Lion "can't do the [[pbas]] tags".  The
recorded evidence pointed two ways -- the Lion input-mode tests say
`[[pbas 60]]` worked when stripping was live; a Leopard docstring says pbas
"reached the engine and did nothing useful", though about the sibling
add-on's classic engines -- so this measures it on the real engine rather
than adjudicating from notes.

Measured by autocorrelation, the same instrument the pitch-slider test uses.
`pbas` is a musical scale, twelve units to the octave, not hertz; 40 and 56 straddle Fred's own base of about
48 and stay inside the detector's range -- 30 put Lion's Fred below 60 Hz and
the autocorrelation locked onto a harmonic, which is how this test first
failed while the engine was doing exactly what was asked.
"""
import struct

import pytest

from synthDrivers._panthera import pantheradriver


def _f0(pcm):
    n = len(pcm) // 2
    v = struct.unpack("<%dh" % n, pcm)
    w = int(22050 * 0.2)
    best, bi = -1, 0
    for i in range(0, max(1, n - w), w // 2):
        e = sum(abs(x) for x in v[i:i + w])
        if e > best:
            best, bi = e, i
    seg = [float(x) for x in v[bi:bi + w]]
    m = sum(seg) / len(seg)
    seg = [x - m for x in seg]
    bc, bl = 0.0, 0
    for lag in range(22050 // 500, 22050 // 40):
        c = sum(seg[i] * seg[i + lag] for i in range(0, len(seg) - lag))
        if c > bc:
            bc, bl = c, lag
    return 22050.0 / bl if bl else 0.0


TEXT = "Hello there, this is a test of the pitch."


def _formant_voice(driver):
    """Fred, the formant voice the octave claims are measured on."""
    for vid in driver._get_availableVoices():
        if vid.lower().startswith("fred"):
            return vid
    pytest.skip("no Fred in this tree")


def test_pbas_moves_the_fundamental_when_commands_are_on(driver):
    voice = _formant_voice(driver)
    driver._acceptCommands = True
    try:
        driver._render(TEXT, 180, voice, 0)          # warm the host
        plain = _f0(driver._render(TEXT, 180, voice, 0))
        low = _f0(driver._render("[[pbas 40]] " + TEXT, 180, voice, 0))
        high = _f0(driver._render("[[pbas 56]] " + TEXT, 180, voice, 0))
    finally:
        driver._acceptCommands = False
    assert low < plain < high, \
        "pbas did not move Fred: 40 -> %.1f, plain %.1f, 56 -> %.1f" % (
            low, plain, high)


def test_pbas_is_inert_with_commands_off(driver):
    """Off by default on purpose: a page containing "[[" must not retune
    the screen reader.  The tag is stripped, not obeyed."""
    voice = _formant_voice(driver)
    driver._acceptCommands = False
    driver._render(TEXT, 180, voice, 0)
    plain = _f0(driver._render(TEXT, 180, voice, 0))
    tagged = _f0(driver._render("[[pbas 60]] " + TEXT, 180, voice, 0))
    assert abs(tagged - plain) < plain * 0.03, (plain, tagged)
