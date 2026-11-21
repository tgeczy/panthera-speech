# -*- coding: utf-8 -*-
"""Render a fixed corpus through one build of the NVDA add-on, and compare two.

**What a release must not change is how the voices sound.**  The engines spoke
correctly before any of the host audit's fixes (Tomi, 2026-11-21), so a new
build should render every text here byte for byte as the last release did --
except where a quirk was fixed on purpose, and then each difference has to be
one somebody can name.

Each side runs in its own process, as NVDA would load it: the `synthDrivers`
package is pointed at that build's add-on folder, so its own Python *and* its
own `panthera_host.exe` render.  What is compared is the driver's `_render`,
which applies everything the add-on does to text on the way to the engine --
numbers, abbreviations, stress, the command fence -- and then the engine's
audio.  NVDA itself is the test suite's stand-in (panthera/tests/conftest.py).

Every item is rendered twice per side.  Lion's engine is not byte-reproducible
from run to run (see the Lion notes), so a side whose two takes differ is
"unstable" for that item and is compared by length, not by bytes.

    py -3 tools/sound_corpus.py run --addon <add-on folder> --out <dir>
    py -3 tools/sound_corpus.py compare <baseline dir> <candidate dir> --again <repeat of baseline> --candidate-again <repeat of candidate>

**Run each side twice** and pass the second runs as --again and --candidate-again: what differs
between two runs of the same build is the engine's own variation, not a
change, and is listed apart from the real differences.  Measured on 3.3.3
(2026-11-21): Lion varies by a few to a few hundred bytes between fresh
starts, and 3.3.3's Snow Leopard Alex spilled audio between utterances
(panthera-speech#26) differently each run.

`--addon` is the folder holding `synthDrivers` -- an unpacked .nvda-addon, or
panthera/addon in this repository.  The engine trees are found the way the
drivers always find them (LEOPARD_TREE and friends, or NVDA's macintalk).
"""
import argparse
import hashlib
import importlib
import json
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.join(os.path.dirname(HERE), "panthera", "tests")

#: The texts.  Ordinary speech first, then every kind of text the add-on
#: rewrites or fences, then the quirks fixed in the 2026-11 audit.
TEXTS = [
    "Hello there.",
    "The quick brown fox jumps over the lazy dog.",
    "Is this working? Yes! It is, mostly...",
    "Panthera speaks with the voices of Mac OS X, from Tiger to Lion.",
    "She said \"come here\" (and he did); then: nothing.",
    "Note: the meeting moved to Tuesday.",
    "Dr. Kirk met Mr. Spock on St. Patrick's Day.",
    "It weighs 5 kg, costs $12.50, and is 30% off.",
    "1234567", "3222233", "12,345", "-42", "0.5", "3.14159", "1.5x faster",
    "Version v0.7.3 shipped on 2026.01.05.",
    "Agent 007 was born in 1962.",
    "Call 555-0123 or write to someone at example dot com.",
    "1234567890123456789012345678901234567890",
    "Visit https://github.com/tgeczy/panthera-speech for more.",
    "MP3, 5KB, 1,234MB, NASA, iPhone, and USB-C.",
    "Café, naïve, façade, and résumé.",
    "Fish & chips, 50/50, #1, and @home.",
    "Type [[rate 300]] to speed up, or [[volm 0.1]] to whisper.",
    "can't won't shouldn't it's they're",
    "a", "I", "OK", "Hmm.",
    "Tab, Shift+Tab, Control+Alt+Delete.",
    "Line one\nLine two\nLine three.",
    ("The deadline is now only days away, and nobody expects a resolution. "
     "Negotiators met again on Monday, but the talks ended without agreement. "
     "A spokesperson said the next round would begin after the holiday."),
]

#: The voices per generation: the formant voices everyone means, Alex where
#: he exists, and one of each other engine.
VOICES = {
    "tiger": ["Fred", "Victoria", "Bruce", "Vicki"],
    "leopard": ["Alex", "Fred", "Victoria", "Bruce", "Vicki"],
    "snowleopard": ["Alex", "Fred", "Vicki"],
    "lion": ["Alex", "Fred", "Vicki"],
}


def _load(addon):
    """Point `synthDrivers` at this build, then stand NVDA up around it."""
    pkg = types.ModuleType("synthDrivers")
    pkg.__path__ = [os.path.join(addon, "synthDrivers")]
    sys.modules["synthDrivers"] = pkg
    sys.path.insert(0, TESTS)
    import conftest  # noqa: F401  -- installs the stand-in NVDA modules


def run(addon, out, generations, repeats):
    _load(addon)
    os.makedirs(out, exist_ok=True)
    results = {"addon": os.path.abspath(addon), "texts": TEXTS, "items": {}}
    for gen in generations:
        try:
            mod = importlib.import_module("synthDrivers.%sspeech" % gen)
            driver = mod.SynthDriver()
        except Exception as e:                      # no tree here, most often
            print("%s: not rendered (%s)" % (gen, e), file=sys.stderr)
            continue
        try:
            available = set(driver.availableVoices)
            prepare = getattr(importlib.import_module("synthDrivers._panthera.host"),
                              "_prepareProse", None)
            for voice in VOICES[gen]:
                if voice not in available:
                    print("%s: no voice %s" % (gen, voice), file=sys.stderr)
                    continue
                for i, text in enumerate(TEXTS):
                    takes = []
                    for _ in range(repeats):
                        chunks = []

                        def sink(chunk, chunks=chunks):
                            chunks.append(bytes(chunk))
                            return True
                        try:
                            got = driver._render(text, driver._wpm(), voice, sink=sink)
                            pcm = b"".join(chunks) if got is not None else None
                        except Exception as e:
                            pcm = None
                            print("%s/%s/%d raised %r" % (gen, voice, i, e), file=sys.stderr)
                        takes.append(pcm)
                    key = "%s/%s/%d" % (gen, voice, i)
                    item = {"len": [None if t is None else len(t) for t in takes],
                            "sha": [None if t is None else hashlib.sha256(t).hexdigest() for t in takes]}
                    if prepare and gen != "tiger":
                        try:
                            item["prepared"] = prepare(text, driver._numberStyle,
                                                       driver._expandAbbreviations,
                                                       driver._fixStress)[0]
                        except Exception as e:
                            item["prepared"] = "<%r>" % (e,)
                    if takes[0] is not None:
                        path = os.path.join(out, key.replace("/", "_") + ".pcm")
                        with open(path, "wb") as f:
                            f.write(takes[0])
                    results["items"][key] = item
                print("%s/%s: %d texts" % (gen, voice, len(TEXTS)), file=sys.stderr)
        finally:
            driver.terminate()
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, ensure_ascii=False)


def _stable(item):
    return len(set(item["sha"])) == 1


def _results(d):
    with open(os.path.join(d, "results.json"), encoding="utf-8") as f:
        return json.load(f)


def _differs(x, y, slack):
    if _stable(x) and _stable(y):
        return x["sha"][0] != y["sha"][0]
    # Unstable: Lion can say one line two ways, and both builds say both.
    # The same only if some take of each side agrees, within the slack.
    return not any(abs((lx or 0) - (ly or 0)) <= slack
                   for lx in x["len"] for ly in y["len"])


def compare(a_dir, b_dir, slack, again=(), candidate_again=()):
    a, b = _results(a_dir), _results(b_dir)
    #: What the engine varies by itself: items that already differ between
    #: the baseline and a repeat of a run (Lion, from one fresh start to the
    #: next, and a build that spills audio between utterances).
    noisy = set()
    for side, repeats in ((a, again), (b, candidate_again)):
        for d in repeats:
            r = _results(d)
            noisy |= {k for k in side["items"] if k in r["items"]
                      and _differs(side["items"][k], r["items"][k], slack)}
    texts = b["texts"]

    #: **Silence is never "the same".**  Two builds that both say nothing
    #: compare equal byte for byte, and that hid Leopard losing a 40-digit
    #: number on every voice in 3.3.3 and since.  Any take with no audio is
    #: named here, whatever the other side did.
    def silent(item):
        return any(not n for n in item["len"])
    mute = sorted(k for k, y in b["items"].items() if silent(y))
    spoke = sorted(k for k, x in a["items"].items() if silent(x) and k in b["items"]
                   and not silent(b["items"][k]))
    for key in mute:
        print("SILENT in the candidate: %s  %r" % (key, texts[int(key.split("/")[2])]))
    for key in spoke:
        print("now speaks (silent in the baseline): %s" % key)
    same, unstable, varies, differ, missing = [], [], [], [], []
    for key in sorted(set(a["items"]) | set(b["items"])):
        x, y = a["items"].get(key), b["items"].get(key)
        if not x or not y:
            missing.append(key)
            continue
        if not _differs(x, y, slack):
            (same if _stable(x) and _stable(y) else unstable).append(key)
        elif key in noisy:
            varies.append(key)
        else:
            differ.append(key)
    print("identical: %d   unstable but the same length (within %d bytes): %d   "
          "varies by itself in a repeat run: %d   DIFFERENT: %d   only on one side: %d"
          % (len(same), slack, len(unstable), len(varies), len(differ), len(missing)))
    for key in varies:
        print("varies by itself: %s" % key)
    for key in differ:
        x, y = a["items"][key], b["items"][key]
        gen, voice, i = key.split("/")
        print("\n%s  %r" % (key, texts[int(i)]))
        print("   length %s -> %s bytes; takes stable: %s -> %s"
              % (x["len"][0], y["len"][0], _stable(x), _stable(y)))
        if x.get("prepared") != y.get("prepared"):
            print("   text sent:  %r\n           -> %r" % (x.get("prepared"), y.get("prepared")))
    for key in missing:
        print("only on one side: %s" % key)
    return 1 if differ or mute else 0


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--addon", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--generations", default="tiger,leopard,snowleopard,lion")
    r.add_argument("--repeats", type=int, default=2)
    c = sub.add_parser("compare")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.add_argument("--slack", type=int, default=64,
                   help="bytes of length difference allowed for an unstable item")
    c.add_argument("--again", action="append", default=[],
                   help="a repeat run of the baseline: what differs there is the engine's own variation")
    c.add_argument("--candidate-again", action="append", default=[],
                   help="a repeat run of the candidate, the same way")
    args = p.parse_args()
    if args.cmd == "run":
        started = time.time()
        run(args.addon, args.out, args.generations.split(","), args.repeats)
        print("rendered in %.0f s" % (time.time() - started), file=sys.stderr)
        return 0
    return compare(args.baseline, args.candidate, args.slack, args.again, args.candidate_again)


if __name__ == "__main__":
    sys.exit(main())
