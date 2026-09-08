"""Diff the host's number rewriting against the Python it was ported from.

`pantheranumbers.py` is the reference and stays the reference.  The C in
`tiger_host_numbers.c` exists so that SAPI and Android reach the same rules the
NVDA driver has always had -- and a port is only worth having if it is exact,
which fifteen hand-written assertions cannot establish for a tokenizer with
two lookarounds and a repeating group.

So generate a corpus that leans on every edge the regex has, run both, and
diff byte for byte.

    py -3 tools/numbers_oracle.py [path\\to\\tiger_host.exe]

Exit status is the number of disagreements, so it can gate a build.
"""
import itertools
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "panthera", "addon", "synthDrivers"))
from _panthera import pantheranumbers  # noqa: E402


def corpus():
    """Every shape the token rule has an opinion about."""
    out = []

    # Digit runs either side of the seven-digit cliff, and well past it.
    for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 18, 19, 20, 25):
        out.append("1" * n)
        out.append("9" * n)
        out.append("1" + "0" * (n - 1))

    # Already grouped, partly grouped, and grouped wrongly.
    out += ["1,234", "1,234,567", "12,34,567", "1,2,3", "1234,567", ",123",
            "123,", "1,234MB", "5KB"]

    # Decimals: leading zero, no leading zero, long fractions, trailing dot.
    out += ["0.5", "0.75", "0.05", "00.5", "1.5", "10.7", "3.14", "0.",
            "1.", ".5", "0.0", "1.23456789", "0.000001"]

    # Versions -- the repeating group.  Two dots, three, four, and one with a
    # part that is too large to say.
    out += ["0.7.3", "1.2.3", "1.2.3.4", "10.20.30", "0.7.3.9.11",
            "1.999999999999999999.2", "v0.7.3", "version 0.7.3"]

    # Signs, including the ones that are not signs.
    out += ["-1234567", "-0.5", "-1", "--1234567", "a-1234567", "1-2",
            "1234567-", "- 1234567"]

    # The lookarounds, from both sides, with every neighbouring class.
    for left in ("", " ", "a", "Z", "9", ",", ".", "(", "-", "é"):
        for right in ("", " ", "a", "Z", "9", ",", ".", ")", "%"):
            out.append(left + "1234567" + right)
            out.append(left + "1.5" + right)
            out.append(left + "0.7.3" + right)

    # Words in sentences, several numbers at once, and text that must survive.
    out += [
        "it rose to 3222233 units in 1999",
        "call 5551234567 now",
        "1234567 and 7654321 and 12",
        "MP3 H2O 20ish B12",
        "the 1,234,567th of 0.7.3 on 0.5",
        "no numbers here at all",
        "",
        " ",
        "1234567 1234567 1234567",
    ]

    # Embedded commands: the host must not rewrite inside one, and the Python
    # never sees one because the driver splits them off first.  Those cases
    # are checked in the C self-test instead, so keep them out of the diff.
    return [t for t in out if "[[" not in t]


def host_expand(exe, style, text):
    """Hex in, hex out.

    The host speaks MacRoman and a Windows command line does not, so handing
    it an accented character as an argument re-encodes it on the way in and
    the diff becomes a diff of the transport.  It did: 81 cases that printed
    identically and compared unequal.  Encode both directions instead.
    """
    payload = text.encode("mac_roman", "replace").hex()
    r = subprocess.run([exe, "--numbers", style, payload], capture_output=True)
    if r.returncode != 0:
        raise SystemExit("host failed on %r: %s" % (text, r.stderr[:200]))
    return bytes.fromhex(r.stdout.decode("ascii").strip()).decode("mac_roman")


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "build", "tiger_host.exe")
    if not os.path.exists(exe):
        raise SystemExit("no host at %s -- run build.sh" % exe)

    cases = corpus()
    bad = 0
    checked = 0
    for style in ("off", "fix", "words"):
        for text in cases:
            want = pantheranumbers.expand(text, style)
            got = host_expand(exe, style, text)
            checked += 1
            if got != want:
                bad += 1
                if bad <= 20:
                    print("DIFFER  [%s] %r" % (style, text))
                    print("   python: %r" % want)
                    print("   host  : %r" % got)
    print("%d cases, %d disagreement(s)" % (checked, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
