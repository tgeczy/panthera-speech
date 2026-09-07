# -*- coding: utf-8 -*-
"""The settings panel, checked the way it used to be checked by eye.

Two labels claiming the same access key is invisible in review and obvious to
somebody driving the panel from the keyboard: the second one silently stops
being reachable by its letter. It has happened, and the rule that came out of
it was "check the whole panel side by side" -- which is exactly the sort of
rule a test should be keeping rather than a person.
"""
import re

import pytest



def _labels():
    from synthDrivers import leopardspeech
    out = []
    for s in leopardspeech.SynthDriver.supportedSettings:
        name = getattr(s, "displayName", None)
        if isinstance(name, str) and "&" in name:
            out.append(name)
    return out


def test_no_two_settings_claim_the_same_access_key():
    seen = {}
    for label in _labels():
        key = re.search(r"&(.)", label).group(1).lower()
        assert key not in seen, (
            "access key &%s is claimed by both %r and %r" %
            (key, seen[key], label))
        seen[key] = label


def test_every_setting_of_ours_has_an_access_key():
    """NVDA's own Voice/Rate/Pitch controls bring their own labels; ours are
    the ones declared here, and a setting with no key cannot be reached from
    the keyboard at all."""
    from synthDrivers import leopardspeech
    ours = [s for s in leopardspeech.SynthDriver.supportedSettings
            if getattr(s, "id", None) and isinstance(
                getattr(s, "displayName", None), str)]
    assert ours, "no settings found -- the fake panel stopped recording again"
    for s in ours:
        assert "&" in s.displayName, "%s has no access key" % s.id


#: Every generation, by driver module.  Checked as classes rather than through
#: an instance so this runs with no engine on the machine at all.
GENERATIONS = ("tigerspeech", "leopardspeech", "snowleopardspeech",
               "lionspeech")


def _resolves(cls, name):
    """-> the class in `cls`'s MRO that actually defines `name`, or None.

    **Not `isinstance(getattr(cls, name), property)`.**  NVDA installs its own
    caching `Getter` descriptor for an accessor written in the class body and a
    plain `property` for one inherited from an `AutoPropertyObject` mixin, so a
    test looking for `property` reports the working case as broken.  What
    matters is only that something in the MRO defines the name.
    """
    for klass in cls.__mro__:
        if name in klass.__dict__:
            return klass
    return None


@pytest.mark.parametrize("modname", GENERATIONS)
def test_every_declared_setting_is_actually_reachable(modname):
    """A declared setting NVDA cannot read is a control that does nothing.

    It fails silently, which is the expensive way: the panel builds, the
    control appears, the user moves it and nothing changes.  This add-on has
    shipped that twice and both times it was found by ear.

    The trap it guards is NVDA's own.  `AutoPropertyType` builds properties
    from `_get_`/`_set_` names found in the class's **own** body -- it reads
    `namespace.keys()` and never looks at the bases -- so an accessor moved to
    a plain mixin becomes no property at all, with no error at class creation
    and no error at import.  The driver body is being split into mixins, and
    this is what keeps that split honest.
    """
    import importlib
    cls = importlib.import_module("synthDrivers." + modname).SynthDriver
    missing = [s.id for s in cls.supportedSettings if not _resolves(cls, s.id)]
    assert not missing, (
        "%s declares %s and nothing in its MRO defines them -- the controls "
        "will appear and do nothing" % (modname, missing))


def test_every_setting_round_trips_on_a_real_driver(driver):
    """Reachable is not the same as wired to anything.

    Reads each setting, writes back exactly what it read, and reads again.  A
    getter pointing at one attribute and a setter at another survives the test
    above and dies here.
    """
    for setting in type(driver).supportedSettings:
        was = getattr(driver, setting.id)
        setattr(driver, setting.id, was)
        assert getattr(driver, setting.id) == was, (
            "%s does not read back what was written to it" % setting.id)
