"""Extract one install image's speech data for the SAPI settings tool.

Progress goes to stdout one line at a time -- "NN% message" -- because the
settings tool reads it live to drive a progress bar; everything is printed
with flush so a line exists the moment there is something to say.

A target that already holds an install is refused with exit code 3 and an
`EXISTS` line naming it, unless `--replace` is given -- in which case the old
folder is removed first, so a replace is a replace and never a merge of two
extractions.  The decision to replace belongs to the person, which is why it
is an exit code here and a question in the settings tool.
"""
import os
import shutil
import sys

import pantheradiscs

replace = "--replace" in sys.argv[1:]
args = [a for a in sys.argv[1:] if a != "--replace"]
if len(args) != 2:
    raise SystemExit("usage: extract.py IMAGE DATA_ROOT [--replace]")
disc = pantheradiscs.identify(args[0])
if not disc.usable:
    raise SystemExit(disc.problem or "This image cannot be used.")
target = os.path.join(args[1], disc.generation.key.title())
if os.path.isdir(target) and os.listdir(target):
    if not replace:
        print("EXISTS %s" % target, flush=True)
        sys.exit(3)
    print("0% Removing the previous install...", flush=True)
    shutil.rmtree(target)
pantheradiscs.extract(disc, target,
                      progress=lambda percent, message:
                      print("%d%% %s" % (percent, message), flush=True))
print("100% Finished.", flush=True)
