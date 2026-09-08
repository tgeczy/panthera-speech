"""Run upstream's wrapper generator without Windows shell argument limits."""
from pathlib import Path
import runpy
import sys

script, arguments = sys.argv[1:]
sys.argv = [script, *Path(arguments).read_text(encoding="utf8").splitlines()]
runpy.run_path(script, run_name="__main__")
