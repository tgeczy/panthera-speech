# -*- coding: utf-8 -*-
"""The 64-bit proxy reaches every setting the driver declares.

**This is the test the whole design of `bridge.py` exists to make possible.**
NVDA's `SynthDriverProxy` defines properties for six settings and nothing
else, so every other setting a driver declares has no property on the 64-bit
side: it cannot be read, cannot be written, and breaks the voice dialog if it
is shown.  The shipped example of this conversion hand-lists the missing ones,
and a hand-kept list is a thing to forget -- silently, on the one screen
nobody tests.

So the accessors are generated from `supportedSettings`, and this checks that
what came out covers what went in.  Add a setting to the driver and it is
covered with no further thought; forget to do something the generator needs
and this fails on the next run rather than on somebody's sign-in screen.

The suite is 64-bit and has no `_bridge` package, which is exactly the
situation the proxy is built for and cannot be exercised in.  So NVDA's half
is faked here -- only the parts `bridge.py` touches, which is a short list.
"""
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, "addon", "synthDrivers")

#: The six NVDA's own proxy already provides; ours must not shadow them.
BRIDGE_NATIVE = ("voice", "variant", "rate", "rateBoost", "pitch", "volume")


def _installFakeBridge():
    """NVDA's `_bridge`, as much of it as `bridge.py` looks at."""
    if "_bridge" in sys.modules:
        return sys.modules[
            "_bridge.clients.synthDriverHost32.synthDriver"].SynthDriverProxy32

    from synthDriverHandler import SynthDriver as FakeSynthDriver

    class SynthDriverProxy32(FakeSynthDriver):
        """Stands in for NVDA's, with the six it really does define."""

        _remoteService = None

        @classmethod
        def check(cls):
            return True

        def _get_supportedSettings(self):
            return list(self._remoteService.getSupportedSettings())

    # The six, installed the way NVDA's are -- through the metaclass, so that
    # `hasattr(base, "rate")` answers the way it does in NVDA.  If this fake
    # got that wrong the generator would either shadow a working property or
    # skip one it must provide, and the test would prove the opposite of what
    # it claims.
    for settingId in BRIDGE_NATIVE:
        def getter(self, _id=settingId):
            return self._remoteService.getParam(_id)

        def setter(self, value, _id=settingId):
            self._remoteService.setParam(_id, value)

        setattr(SynthDriverProxy32, "_get_" + settingId, getter)
        setattr(SynthDriverProxy32, "_set_" + settingId, setter)
        setattr(SynthDriverProxy32, settingId,
                property(getter, setter))

    pkg = types.ModuleType("_bridge")
    pkg.__path__ = []
    clients = types.ModuleType("_bridge.clients")
    clients.__path__ = []
    host = types.ModuleType("_bridge.clients.synthDriverHost32")
    host.__path__ = []
    mod = types.ModuleType("_bridge.clients.synthDriverHost32.synthDriver")
    mod.SynthDriverProxy32 = SynthDriverProxy32
    sys.modules.update({
        "_bridge": pkg,
        "_bridge.clients": clients,
        "_bridge.clients.synthDriverHost32": host,
        "_bridge.clients.synthDriverHost32.synthDriver": mod,
    })
    return SynthDriverProxy32


def _resolves(cls, name):
    """-> True if anything in the MRO defines `name`.

    Not an `isinstance(..., property)` check: NVDA installs its own caching
    descriptor for an accessor written in a class body, so looking for
    `property` reports the working case as broken.
    """
    return any(name in klass.__dict__ for klass in cls.__mro__)


GENERATIONS = ["tigerspeech", "leopardspeech", "snowleopardspeech",
               "lionspeech"]


@pytest.fixture(params=GENERATIONS)
def proxy(request):
    from synthDrivers._panthera import bridge
    _installFakeBridge()
    module = __import__("synthDrivers." + request.param, fromlist=["x"])
    # `module.SynthDriver` is the real driver here: the executable exists on
    # this machine, so `driverFor` hands back the class unchanged and never
    # builds a proxy of its own.  Taking it this way rather than reaching for
    # `PantheraDriver` also covers Tiger, whose driver predates the shared
    # body and descends from nothing in common with the other three.
    real = module.SynthDriver
    if not os.path.isfile(module.HOST_EXE):
        pytest.skip("no panthera_host.exe; the module may have bound a proxy")
    return real, bridge.proxyFor(real, request.param, ADDON)


def test_every_declared_setting_is_reachable_through_the_bridge(proxy):
    real, cls = proxy
    missing = []
    for setting in real.supportedSettings:
        for accessor in ("_get_" + setting.id, "_set_" + setting.id):
            if not _resolves(cls, accessor):
                missing.append(accessor)
    assert not missing, (
        "the bridge proxy cannot reach %s -- on a secure screen these "
        "settings would silently do nothing" % ", ".join(missing))


def test_string_settings_bring_their_option_list(proxy):
    """A choice setting with no list to choose from breaks the voice dialog.

    The bridge forwards `getParam`/`setParam` for anything, but never the
    lists, so every one has to be provided on this side.
    """
    real, cls = proxy
    from autoSettingsUtils.driverSetting import DriverSetting

    missing = []
    for setting in real.supportedSettings:
        if type(setting) is not DriverSetting:
            continue
        name = "available%ss" % setting.id.capitalize()
        if not _resolves(cls, "_get_" + name):
            missing.append(name)
    assert not missing, (
        "no option list for %s; opening the voice dialog would raise"
        % ", ".join(missing))


def test_the_six_the_bridge_owns_are_left_alone(proxy):
    """Ours must not shadow NVDA's own, which already work."""
    _, cls = proxy
    shadowed = [s for s in BRIDGE_NATIVE if "_get_" + s in cls.__dict__]
    assert not shadowed, (
        "the proxy redefines %s, which NVDA's own proxy already provides"
        % ", ".join(shadowed))


def test_it_is_the_same_synthesizer_not_a_second_one(proxy):
    """Name and description are carried across unchanged.

    A different `name` would be a different key in NVDA's configuration:
    everybody's stored voice, rate and pitch for this generation would be
    silently abandoned the first time they used a secure screen.
    """
    real, cls = proxy
    assert cls.name == real.name
    assert cls.description == real.description
    assert cls.synthDriver32Name == real.name


@pytest.mark.parametrize("module", GENERATIONS)
def test_the_module_exposes_exactly_one_synthDriver(module):
    """One entry in the synthesizer list, whichever class is bound.

    NVDA lists a module once, by finding `SynthDriver` in it.  The worry this
    settles is real -- four generations becoming eight, a bridged twin beside
    each -- and the answer is that there is only ever one name to find.
    """
    mod = __import__("synthDrivers." + module, fromlist=["x"])
    drivers = [name for name, value in vars(mod).items()
               if isinstance(value, type)
               and name.lower().endswith("synthdriver")]
    assert drivers == ["SynthDriver"], (
        "%s exposes %r; NVDA would list one synthesizer per name here"
        % (module, drivers))


@pytest.mark.parametrize("module", GENERATIONS)
def test_the_desktop_keeps_the_real_driver(module):
    """With an executable present, nothing is substituted at all.

    The whole point of the shape Tomi chose: on an ordinary desktop this is
    the same driver it has always been, and the bridge is not in the picture.
    """
    from synthDrivers._panthera import bridge
    mod = __import__("synthDrivers." + module, fromlist=["x"])
    if not os.path.isfile(mod.HOST_EXE):
        pytest.skip("no panthera_host.exe built; run sh build.sh")
    assert bridge.driverFor(mod.SynthDriver, module, ADDON,
                            mod.HOST_EXE, mod.HOST_DLL) is mod.SynthDriver
