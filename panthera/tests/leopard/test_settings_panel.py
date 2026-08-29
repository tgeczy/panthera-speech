# -*- coding: utf-8 -*-
"""The settings panel, checked the way it used to be checked by eye.

Two labels claiming the same access key is invisible in review and obvious to
somebody driving the panel from the keyboard: the second one silently stops
being reachable by its letter. It has happened, and the rule that came out of
it was "check the whole panel side by side" -- which is exactly the sort of
rule a test should be keeping rather than a person.
"""
import os
import re
import sys

_ADDON = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "addon", "synthDrivers")
sys.path.insert(0, os.path.join(_ADDON, "_panthera"))
sys.path.insert(0, _ADDON)


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
