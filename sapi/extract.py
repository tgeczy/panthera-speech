import os, sys
import pantheradiscs

if len(sys.argv) != 3:
    raise SystemExit("usage: extract.py IMAGE DATA_ROOT")
disc = pantheradiscs.identify(sys.argv[1])
if not disc.usable:
    raise SystemExit(disc.problem or "This image cannot be used.")
target = os.path.join(sys.argv[2], disc.generation.key.title())
pantheradiscs.extract(disc, target,
                      progress=lambda percent, message:
                      print("%d%% %s" % (percent, message), flush=True))
