# -*- coding: utf-8 -*-
"""Turning off "Expand abbreviations" has to turn off the ones people meet.

Tomi, testing something else entirely:

    "2-letter acronyms like DR read as 'doctor', and someone may not genuinely
    want the first two letters to get expanded."

The setting existed and did not cover it.  `TIGER_NO_ABBREV` declines to
compile the `SpeechDictionary` rules that rewrite units and quantities -- 5KB
into kilobytes, 20ish into twentyish -- and those are regular expressions this
host compiles, so refusing one turns it off.  "DR" never reaches them: it is a
lexical entry inside MacinTalk, and rendering it runs no regular expression and
no SQL query at all.

The other half of the same setting is in
`tests/leopard/test_abbreviations.py`, which covers the dictionary rules and
the unterminated-buffer bug that made them fire only sometimes.  Two
mechanisms, one checkbox -- which is exactly how half of it went unnoticed.

Two halves are tested here and they need each other:

* `test_the_table_is_what_the_engine_actually_does` asks the engine, so the
  table cannot quietly drift from the thing it describes -- and it is the test
  that would have caught the entries never being right in the first place.
* the rest are plain text tests of the rule, which is where the judgement
  lives: only capitals, never a trailing stop, and `vs` and `etc` as written in
  prose are left alone.
"""
import os
import struct
import subprocess

import pytest

import pantheraabbrev


# -- the rule -------------------------------------------------------------

@pytest.mark.parametrize("token", sorted(pantheraabbrev.ACRONYMS))
def test_capitals_are_spelt_out(token):
    assert pantheraabbrev.spell(token) == " ".join(token)


@pytest.mark.parametrize("written", ["Dr", "dr", "Mr", "vs", "etc", "St"])
def test_an_abbreviation_written_as_one_is_left_alone(written):
    """`vs` and `etc` mean what the engine says they mean.

    Reading "Ali vs Frazier" as "Ali V S Frazier" would be a plain regression,
    and it is the reason this is case-sensitive rather than thorough.
    """
    assert pantheraabbrev.spell(written) == written


def test_a_trailing_stop_keeps_the_expansion():
    """The stop is the tell that somebody wrote an abbreviation."""
    assert pantheraabbrev.spell("DR.") == "DR."
    assert pantheraabbrev.spell("ST. LOUIS") == "ST. LOUIS"


def test_only_whole_words():
    for text in ("DRUM", "ADR", "MRS_SMITH", "CTRL", "FTP"):
        assert pantheraabbrev.spell(text) == text, text


def test_it_works_inside_a_sentence():
    assert pantheraabbrev.spell("the DR and ST fields") == \
        "the D R and S T fields"


def test_nothing_else_is_touched():
    text = "Meeting at 5KB, 1,234MB, room 20ish -- Dr. Who vs. the Master."
    assert pantheraabbrev.spell(text) == text


# -- the table, against the engine ----------------------------------------

REQ, RSP = 0x54475233, 0x54475253


def _render(proc, text):
    t, v = text.encode("utf-8"), b"Fred"
    proc.stdin.write(struct.pack("<IiIIII", REQ, 180, 0, 0, len(v), len(t))
                     + v + t)
    proc.stdin.flush()
    head = proc.stdout.read(12)
    assert len(head) == 12, "the host closed the pipe"
    _magic, _status, nframes = struct.unpack("<IiI", head)
    out = b""
    while len(out) < nframes * 2:
        chunk = proc.stdout.read(nframes * 2 - len(out))
        assert chunk, "the response was short"
        out += chunk
    return out


@pytest.fixture(scope="module")
def host():
    import leopardspeech
    tree = leopardspeech.find_tree()
    if not tree:
        pytest.skip("no Leopard speech tree")
    if not os.path.isfile(leopardspeech.HOST_EXE):
        pytest.skip("panthera_host.exe not built")
    mt, sd, voices = leopardspeech.engine_paths(tree)
    p = subprocess.Popen([leopardspeech.HOST_EXE, "--serve", mt, sd, voices],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    yield p
    p.kill()


@pytest.mark.parametrize("token", sorted(pantheraabbrev.ACRONYMS))
def test_the_table_is_what_the_engine_actually_does(host, token):
    """Every entry must really be expanded, and spelling it must really stop it.

    Asked of the engine rather than asserted: the token and its expansion come
    back byte-identical, and the spelt-out form does not.  An entry the engine
    does not expand would be this module rewriting text for no reason.
    """
    word = pantheraabbrev.ACRONYMS[token]
    assert _render(host, token) == _render(host, word), (
        "%s is not expanded to %r by the engine, so spelling it out changes "
        "a reading that was already correct" % (token, word))
    assert _render(host, pantheraabbrev.spell(token)) != _render(host, word), \
        "spelling %s out did not stop the expansion" % token
