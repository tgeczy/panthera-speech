# -*- coding: utf-8 -*-
"""The same synthesizer, reached through NVDA's own 32-bit bridge.

**Only where there is no executable.**  On an ordinary desktop this module
does nothing at all: the driver spawns `panthera_host.exe` as it always has,
and a subprocess does not care what bitness NVDA is.  It matters on secure
screens, where NVDA drops every `.exe` on its way into `systemConfig` and the
engine has to be the DLL instead -- and a DLL of i386 code cannot be loaded by
64-bit NVDA at all.  NVDA 2026.1 supplies the missing half: a 32-bit host
process of its own that loads a synth driver and proxies it back.

**One module, one synthesizer, always.**  This does not add a "bridged"
entry beside the real one.  `synthDrivers/leopardspeech.py` binds the name
`SynthDriver` to one class or the other, and NVDA finds exactly one
`SynthDriver` per module, under one `name`, keyed to one set of stored
settings.  Nobody sees double and nobody's voice or rate moves.

**The accessors are generated, not listed.**  NVDA's proxy defines properties
for six settings -- voice, variant, rate, rateBoost, pitch and volume -- and
nothing else, so every other setting a driver declares would have no property
on the 64-bit side: unreadable, unwritable, and fatal to the voice dialog if
shown.  The shipped example of this conversion hand-lists the missing ones.
That list is a thing to forget, and forgetting it is silent: the setting simply
stops applying, on the one screen nobody tests.  So the list is taken from the
driver's own `supportedSettings` instead, which is the same declaration the
settings dialog is built from and cannot drift from it.

`_get_available*s` for the string settings comes across the same way, by
copying the real driver's own -- those return fixed option lists and touch
nothing but `TITLE`, which is copied with them.  The bridge does not forward
them, and a string setting without one breaks the dialog.
"""
import ctypes
import os

from logHandler import log

from . import diagnostics


def _proxyBase():
    """-> NVDA's 32-bit synth-driver proxy, or None if this NVDA has none.

    **Guarded, and the guard is load-bearing twice.**  `_bridge` arrived in
    NVDA 2026.1, so on anything older the import raises -- and an unguarded
    import at module scope would take the whole driver module down with it,
    losing the synthesizer entirely for people whose executable works
    perfectly.  It is also absent from the test suite, where an unguarded
    import would fail every driver module at collection.
    """
    try:
        from _bridge.clients.synthDriverHost32.synthDriver import (
            SynthDriverProxy32)
    except Exception:
        return None
    return SynthDriverProxy32


def available():
    """-> True when the bridge exists here and its 32-bit host is installed."""
    base = _proxyBase()
    try:
        return bool(base is not None and base.check())
    except Exception:
        return False


def _accessorsFor(settingId):
    """A `_get_`/`_set_` pair that asks the far side.

    `getParam`/`setParam` are generic and accept any setting the remote driver
    declares, so nothing here needs to know what the setting means.
    """
    def getter(self):
        return self._remoteService.getParam(settingId)

    def setter(self, value):
        self._remoteService.setParam(settingId, value)

    getter.__name__ = "_get_" + settingId
    setter.__name__ = "_set_" + settingId
    return getter, setter


def _listName(settingId):
    """`phrasing` -> `availablePhrasings`, NVDA's own spelling."""
    return "available%ss" % settingId.capitalize()


def proxyFor(real, moduleName, path):
    """Build the class a 64-bit NVDA should load in place of `real`.

    `real` is the driver as it runs in the 32-bit host; `moduleName` and
    `path` tell the bridge which module to load there, and must name this
    add-on's own `synthDrivers` folder.
    """
    base = _proxyBase()
    namespace = {
        "name": real.name,
        "description": real.description,
        "synthDriver32Path": path,
        "synthDriver32Name": moduleName,
        # Two of the option-list getters phrase themselves with it -- "%s's
        # own" -- so it travels with them.
        "TITLE": getattr(real, "TITLE", real.name),
    }

    reachable = []
    for setting in real.supportedSettings:
        settingId = setting.id
        if hasattr(base, settingId):
            # One of the six the bridge already handles.  Ours would shadow a
            # working property with an identical one, so leave it alone.
            reachable.append(settingId)
            continue
        (namespace["_get_" + settingId],
         namespace["_set_" + settingId]) = _accessorsFor(settingId)
        listGetter = getattr(real, "_get_" + _listName(settingId), None)
        if listGetter is not None:
            namespace["_get_" + _listName(settingId)] = listGetter
        reachable.append(settingId)

    def _get_supportedSettings(self):
        """The remote's list, minus anything this side cannot actually show.

        A safety net rather than a filter that does work: everything declared
        is generated above.  It earns its place the day somebody adds a string
        setting and no option list to go with it -- without this the voice
        dialog raises when it is opened, and with it the setting is merely
        missing until somebody notices.
        """
        from autoSettingsUtils.driverSetting import DriverSetting

        cls = type(self)
        usable = []
        for setting in base._get_supportedSettings(self):
            if not hasattr(cls, setting.id):
                continue
            # A plain `DriverSetting` is a choice from a list, and the list is
            # the one thing the bridge does not forward.  Showing one without
            # it raises when the voice dialog is opened.
            if (type(setting) is DriverSetting
                    and not hasattr(cls, _listName(setting.id))):
                continue
            usable.append(setting)
        return usable

    namespace["_get_supportedSettings"] = _get_supportedSettings

    def __init__(self):
        """Start the far side, and write down anything that stops it.

        **The recording is the whole reason this method exists.**  NVDA says
        no more than "could not load" when a synthesizer refuses, and on a
        secure screen that is the end of the road: no debug logging, and a log
        belonging to SYSTEM.  RPyC does carry the remote traceback, so what
        actually failed inside the 32-bit host arrives here -- and this is the
        only place it passes through code of ours before NVDA discards it.
        """
        try:
            base.__init__(self)
        except Exception as e:
            diagnostics.record(
                "%s could not start through NVDA's 32-bit bridge" % real.name,
                e)
            raise

    namespace["__init__"] = __init__

    def check(cls):
        """Both halves have to be there: the bridge, and an engine to run."""
        try:
            return bool(base.check() and real.check())
        except Exception:
            log.debugWarning("%s: bridge check failed" % real.name,
                             exc_info=True)
            return False

    namespace["check"] = classmethod(check)

    # Built through the metaclass rather than with `class`, because that is
    # what turns these `_get_`/`_set_` names into properties -- and it reads
    # only the namespace it is handed, which is why they have to all be in it
    # before the class exists rather than assigned afterwards.
    return type(base)("SynthDriver", (base,), namespace)


def driverFor(real, moduleName, path, hostExe, hostDll):
    """-> the class this driver module should expose as `SynthDriver`.

    The real driver, unless this is a 64-bit process with no executable to
    spawn -- which is a secure screen, and the only case the bridge exists
    for.  Anything unexpected falls back to the real driver: it may not be
    able to speak, but it explains itself, and a driver module that raises at
    import takes the synthesizer out of the list with no explanation at all.
    """
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        return real                      # we can load the library ourselves
    if os.path.isfile(hostExe):
        return real                      # a subprocess; bitness is irrelevant
    if not os.path.isfile(hostDll) or not available():
        return real
    try:
        return proxyFor(real, moduleName, path)
    except Exception:
        log.error("%s: could not build the bridge proxy; the synthesizer "
                  "will not work on this screen" % real.name, exc_info=True)
        return real
