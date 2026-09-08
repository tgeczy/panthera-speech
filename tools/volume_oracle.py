"""Compare the host's volume mapping to the NVDA driver's measured tables.
Usage: python3 tools/volume_oracle.py build/linux-i686/tiger_host
Requires no Apple engine data.
"""
import ast
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
drivers = root / "panthera/addon/synthDrivers"
def table(path, name):
    tree = ast.parse((drivers / path).read_text(encoding="utf-8"))
    return next(ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name)
                and t.id == name for t in n.targets))

tables = {"tiger": {},
    "leopard": table("_panthera/constants.py", "VOLUME_NORM_LEOPARD"),
    "snowleopard": table("snowleopardspeech.py", "VOLUME_NORM"),
    "lion": table("lionspeech.py", "VOLUME_NORM")}
count = 0
for gen, norms in tables.items():
    for voice in list(norms) + ["Unmeasured voice"]:
        for level in [0, 1, 25, 45, 50, 89, 90, 91, 99, 100]:
            expected = min(2.0, norms.get(voice, 1.0) * level / (100 if gen == "tiger" else 90))
            expected = int(round(float("%.3f" % expected) * 1000))
            actual = int(subprocess.check_output([sys.argv[1], "--volume-value", str(level), gen, voice]))
            assert actual == expected, (gen, voice, level, actual, expected)
            count += 1
print("volume: %d Python/C comparisons passed" % count)
