# -*- coding: utf-8 -*-
"""The shared body of every Panthera synthesizer, as a package.

This folder used to be put on `sys.path` by each of the five driver modules,
and every module in it carried a `panthera` prefix to survive that.  The
prefix was not decoration.  Every NVDA add-on shares one `sys.modules`, so
when this driver and its Tiger sibling both inserted their private folder and
both did `import tree`, whichever loaded first won and the second silently got
the first one's module: Leopard read tigerspeech-data, ran Tiger's host, and
offered Tiger's twenty-three voices under Leopard's name.  Nothing failed.  It
took a user noticing the wrong voices to see it.

**A package retires that whole class of bug rather than naming its way around
it.**  Submodules live under `synthDrivers._panthera.*` and cannot collide in
the flat namespace at all, so somebody still running the old `tigerspeech` or
`leopardspeech` add-on alongside this one is no longer a hazard -- their
private folders may be on `sys.path`, but nothing here is reached through it.

`addonHandler.Addon.addToPackagePath` inserts every add-on's `synthDrivers`
folder into the real `synthDrivers.__path__`, which is what makes
`from ._panthera import ...` work from `leopardspeech.py` and
`from synthDrivers._panthera import ...` work from the global plugin.

The leading underscore is load-bearing in its own way:
`synthDriverHandler.getSynthList()` skips any module whose name starts with
one, so this package is never mistaken for a fifth synthesizer in the list.

The `panthera` prefixes on the module names inside are now redundant and are
being retired module by module as each is split out.
"""
