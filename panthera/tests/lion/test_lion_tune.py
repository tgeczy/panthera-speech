# -*- coding: utf-8 -*-
"""10.7's tune annotations, which died in two thunks for a year of releases.

The dictionary always did its half: `[[inpt TUNE]]` set the lexer mode, the
`{D ...; P t:p}` groups parsed, and the class-9 token carried an SLPhonTune
per phoneme.  `SLHomographCopyTune` then built the melody the engine asked
for -- through `CFDictionaryCreate` and `CFArrayCreate`, neither of which the
host implemented, so the thunks' zeros left CopyTune as "no tune" and the
phonemes spoke plain.  See panthera-speech#6 and the container block in
`tiger_host_cf.c`.

Nothing logged it because serve mode turns the verbose stub logger off while
answering requests, which is the only window in which CopyTune runs.  These
tests are the replacement for that blindness: delete a container shim and
they fail.

Each test still sets `INPUT_MODES_WORK` on its own driver instance -- the
flag is True since 1.1, but these tests are about the HOST, and forcing the
flag keeps them true to that even if the driver's answer ever changes.
"""
import pytest


def _tuned_driver(driver, monkeypatch):
    monkeypatch.setattr(driver, "INPUT_MODES_WORK", True)
    driver._set_voice("Fred")
    driver._acceptCommands = True
    return driver._wpm()


def test_a_duration_annotation_stretches_the_note(driver, monkeypatch):
    """`{D 2000}` is a two-second note; a bare tune phoneme is a blip.

    A ratio rather than a frame count, because Lion's mtk3 voices are not
    reproducible -- the same text and binary rendered 3358 and 3582 frames on
    different days while this bug was being found.  The measured ratio is
    about 45; asking for 8 leaves nondeterminism all the room it has ever
    taken.
    """
    wpm = _tuned_driver(driver, monkeypatch)
    noted = driver._render("[[inpt TUNE]] m{D 2000}", wpm, "Fred")
    bare = driver._render("[[inpt TUNE]] m", wpm, "Fred")
    assert noted and bare
    assert len(noted) > len(bare) * 8, (
        "the duration annotation changed nothing: %d frames against %d -- "
        "the melody is being dropped again (panthera-speech#6)"
        % (len(noted) // 2, len(bare) // 2))


def test_the_reported_sequence_renders_its_pitch_targets(driver, monkeypatch):
    """The repro case from the issue, as a ratio against its own bare note.

    While broken, `m{D 500; P 50:0 50:100}` and bare `m` rendered *identical*
    audio -- the definition of the fault.  Fixed, the annotated form runs
    about twelve times longer (12094 frames against Leopard's 12096, for the
    record); asking for 4 is the same generosity as above.
    """
    wpm = _tuned_driver(driver, monkeypatch)
    annotated = driver._render("[[inpt TUNE]] m{D 500; P 50:0 50:100}", wpm,
                               "Fred")
    bare = driver._render("[[inpt TUNE]] m", wpm, "Fred")
    assert annotated and bare
    assert len(annotated) > len(bare) * 4, (
        "the pitch annotation changed nothing: %d frames against %d"
        % (len(annotated) // 2, len(bare) // 2))
