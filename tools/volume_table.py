# -*- coding: utf-8 -*-
"""Build the per-voice volume table, worst case across several texts.

One sentence is not enough to find a voice's ceiling. Peak is set by whatever
transient happens to be in the text -- a plosive, a shouted capital, the
attack of a sung note -- so a factor measured on prose can clip on a list of
numbers. This takes the LOUDEST peak each voice produces across every probe
and works from that.

Two numbers come out per voice:

    natural   its RMS at volm 1.0, averaged over the probes -- how loud it
              actually sounds today, since the driver sends nothing at 100
    safe      how far it can be turned up before any sample hits the ceiling

and the table the driver wants is built from both:

    norm = max(1.0, min(safe, TARGET / natural))

TARGET is the loudest voice's own level, so quiet voices come up to meet it
and **nothing is ever turned down**. Voices that cannot reach it -- Whisper
most of all, which is supposed to be quiet -- get their own safe maximum and
keep their character.
"""
import array
import io
import os
import math
import sys
import wave

#: Relative to this file rather than to one machine. These were absolute paths
#: into the old leopard-speech checkout, which stopped existing the day the two
#: repositories merged. They were then made relative to `leopard/tools/`, which
#: the merge had dissolved as well -- so the tool still could not run, and
#: nothing said so, because nothing runs it except a person rebuilding a table.
#:
#: It lives in the shared `tools/` now, because two generations need it, and it
#: is told which one on the command line rather than guessing. A table measured
#: on one bank and applied to another is the exact mistake it exists to
#: prevent, and the voice names are no help: twenty-three of them are the same
#: on both.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ADDON = os.path.join(os.path.dirname(_HERE), "panthera")
sys.path.insert(0, os.path.join(_ADDON, "tests"))
import conftest                                                # noqa: E402,F401
sys.path.insert(0, os.path.join(_ADDON, "addon", "synthDrivers"))
sys.path.insert(0, os.path.join(_ADDON, "addon", "synthDrivers", "_panthera"))
import pantheradriver                                          # noqa: E402

#: Which generations have a table to build. Tiger is not one: its engine takes
#: no `volm` and its driver has no volume slider to feed.
#:
#: The name is also the driver module's, minus "speech", which is what lets
#: this be a lookup rather than a table -- and is the reason a fourth
#: generation cost one word here.
GENERATIONS = ("leopard", "snowleopard", "lion")

CEIL = 32767

#: Chosen to provoke different peaks, not to read nicely.
PROBES = [
    "The US Chamber of Commerce warned Tuesday that higher tariffs would "
    "damage both economies and drive up costs for families.",
    "Ah, oh, ooh, aye, awe, oi, ow. WARNING! ERROR! STOP! ATTENTION!",
    "Take a big pack of tickets, Bobby. Peter picked a peck. "
    "one two three four five six seven eight nine ten.",
    # The three of them run together. Not redundant: a peak can come from
    # where one phrase meets the next, and a first table fitted to the probes
    # above clipped on exactly this text.
    "The US Chamber of Commerce warned Tuesday. Ah, oh, ooh, aye. "
    "WARNING! ERROR! Take a big pack of tickets, Bobby.",
    "Zzzz. Sh. Ts. Ks. Ps. Oh! Ah! Eee! Ooo! Aye! Owww! Yes. No. Stop!",
]

#: Kept back from the measured ceiling, because a ceiling fitted to five
#: sentences is not a ceiling for every sentence.
#:
#: This is not caution for its own sake: the first version of this table used
#: the measured maximum exactly, and Whisper clipped 13 samples the moment a
#: test used a text the table had not been fitted to. About 1 dB, which is
#: inaudible, against distortion that is not.
MARGIN = 0.89


def meas(rendered):
    a = array.array("h")
    if rendered[:4] == b"RIFF":
        w = wave.open(io.BytesIO(rendered), "rb")
        a.frombytes(w.readframes(w.getnframes()))
    else:
        a.frombytes(rendered[:len(rendered) // 2 * 2])
    if not len(a):
        return 0, 0.0
    return max(max(a), -min(a)), (sum(float(v) * v for v in a) / len(a)) ** 0.5


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in GENERATIONS:
        print("usage: volume_table.py {%s}" % "|".join(GENERATIONS))
        return 2
    gen = sys.argv[1]
    mod = __import__(gen + "speech")
    if not mod.find_tree():
        print("no %s speech tree; set %s_TREE" % (gen, gen.upper()))
        return 1
    d = mod.SynthDriver()
    d._acceptCommands = True
    stats = {}
    try:
        wpm = d._wpm()
        names = list(d._get_availableVoices())
        for v in names:
            d._set_voice(v)
            peaks, rmss = [], []
            for probe in PROBES:
                # Explicit prefix every time: volm is channel state and
                # outlives the utterance that set it.
                p, r = meas(d._render("[[volm 1.000]]" + probe, wpm, v))
                if p:
                    peaks.append(p)
                    rmss.append(r)
            if not peaks:
                print("  %s rendered nothing" % v)
                continue
            worst = max(peaks)
            # **The driver's ceiling, not the engine's.** This used to clamp
            # at 2.0, which is where the engine stops honouring `volm`, and
            # that is a different number from the highest factor a voice can
            # be given: right at 2.0 the arithmetic stops holding and Whisper
            # clips rather than gaining the 3% it was asked for. Leopard's
            # shipped table was hand-clamped to 1.80 afterwards, so the tool
            # and the table it built had already disagreed -- and pasting this
            # output in would have quietly undone it.
            stats[v] = (sum(rmss) / len(rmss),
                        min(pantheradriver.VOLUME_NORM_CEILING,
                            MARGIN * CEIL / float(worst)), worst)
    finally:
        d.terminate()

    target = max(s[0] for s in stats.values())
    loudest = max(stats, key=lambda k: stats[k][0])
    print("target = %s, the loudest voice, RMS %.0f\n" % (loudest, target))
    print("  %-11s %8s %6s %6s %8s %8s"
          % ("voice", "natural", "worst", "safe", "norm", "change"))
    table = {}
    for v in sorted(stats, key=lambda k: -stats[k][0]):
        natural, safe, worst = stats[v]
        norm = max(1.0, min(safe, target / natural))
        table[v] = round(norm, 2)
        print("  %-11s %8.0f %6d %6.2f %8.2f %+7.1f dB"
              % (v, natural, worst, safe, norm, 20 * math.log10(norm)))

    print("\n#: Measured with tools/volume_table.py -- see its docstring.")
    print("VOLUME_NORM_%s = {" % gen.upper())
    for v in sorted(table):
        print("    %-13s %.2f," % ('"%s":' % v, table[v]))
    print("}")
    lo = min(stats[v][0] * table[v] for v in table if v != "Whisper")
    hi = max(stats[v][0] * table[v] for v in table)
    print("\n# spread after, excluding Whisper: %.1f dB" % (20 * math.log10(hi / lo)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
