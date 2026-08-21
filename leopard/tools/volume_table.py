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

sys.path.insert(0, r"C:\git\leopard-speech\tests")
import conftest                                                # noqa: E402,F401
sys.path.insert(0, r"C:\git\leopard-speech\addon\synthDrivers")

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
    import leopardspeech
    if not leopardspeech.find_tree():
        print("no Leopard speech tree; set LEOPARD_TREE")
        return 1
    d = leopardspeech.SynthDriver()
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
            stats[v] = (sum(rmss) / len(rmss),
                        min(2.0, MARGIN * CEIL / float(worst)), worst)
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

    print("\n#: Measured with tools/volume_table.py. See the module docstring.")
    print("VOLUME_NORM = {")
    for v in sorted(table):
        print("    %-13s %.2f," % ('"%s":' % v, table[v]))
    print("}")
    lo = min(stats[v][0] * table[v] for v in table if v != "Whisper")
    hi = max(stats[v][0] * table[v] for v in table)
    print("\n# spread after, excluding Whisper: %.1f dB" % (20 * math.log10(hi / lo)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
