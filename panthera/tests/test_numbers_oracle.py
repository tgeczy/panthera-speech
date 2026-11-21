"""The host's number rewriting says exactly what the Python reference says.

`tools/numbers_oracle.py` diffs the two byte for byte over a corpus that leans
on every edge the token rule has.  It ran only by hand, and the halves drifted
apart: the host gave up at 64 characters and 9 parts where the Python read on
(panthera-speech#44), and #31's leading-zero rule widened that.  Now the suite
runs it, so the next drift fails here and not in somebody's ear.
"""
import importlib.util
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def _oracle():
    spec = importlib.util.spec_from_file_location(
        "numbers_oracle", os.path.join(ROOT, "tools", "numbers_oracle.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_host_and_the_python_say_every_number_alike():
    from synthDrivers._panthera import pantheraleopard
    host = pantheraleopard.HOST_EXE
    if not os.path.isfile(host):
        pytest.skip("panthera_host.exe not built; run sh build.sh")
    oracle = _oracle()
    differ = []
    for style in ("off", "fix", "words"):
        for text in oracle.corpus():
            want = oracle.pantheranumbers.expand(text, style)
            got = oracle.host_expand(host, style, text)
            if got != want:
                differ.append((style, text, want, got))
    assert not differ, "%d disagreement(s), the first: %r" % (len(differ), differ[0])
