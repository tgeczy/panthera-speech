# -*- coding: utf-8 -*-
"""Comparing our version against the newest published release.

Asked for by Sean, who would rather press a button than watch the repository.

The comparison is the part that can be quietly wrong -- a check that never
finds an update looks exactly like a check that finds none -- so it is tested
against the tag shapes this project has actually used, and against the ones a
future release could produce.  The fetch is not tested: it is deliberately the
smallest piece of code here, and it is handed an `opener` so the parsing
around it can be exercised without a network.
"""
import json

import pytest

from synthDrivers._panthera import updates


@pytest.mark.parametrize("text,expected", [
    ("pantheraspeech/v2.0.0", (2, 0, 0)),   # the tag this project uses
    ("v2.0.0", (2, 0, 0)),
    ("2.0.0", (2, 0, 0)),
    ("1.3.1", (1, 3, 1)),
    ("2.0", (2, 0)),
    ("pantheraspeech/v2.1.0-rc1", (2, 1, 0)),
    ("", None),
    (None, None),
    ("no numbers here", None),
])
def test_versions_are_read_out_of_whatever_shape_the_tag_is(text, expected):
    assert updates.parse_version(text) == expected


@pytest.mark.parametrize("latest,installed", [
    ("2.0.1", "2.0.0"),
    ("2.1.0", "2.0.9"),          # not a string comparison
    ("10.0.0", "9.0.0"),         # nor a lexical one
    ("pantheraspeech/v2.0.0", "1.5.0"),
    ("2.1", "2.0.0"),            # shorter and still newer
])
def test_a_newer_release_is_newer(latest, installed):
    assert updates.is_newer(latest, installed) is True


@pytest.mark.parametrize("latest,installed", [
    ("2.0.0", "2.0.0"),
    ("2.0", "2.0.0"),            # padded, so these tie
    ("2.0.0", "2.0.1"),          # older than what is running
    ("1.0.0", "2.0.0"),
    ("", "2.0.0"),
    ("2.0.0", ""),
    ("nonsense", "2.0.0"),
])
def test_anything_else_is_not_an_update(latest, installed):
    """Including both kinds of unreadable.

    An add-on that cannot tell must say nothing rather than announce an
    update that may not be there -- the failure that wastes somebody's time
    and their trust in the button at once.
    """
    assert updates.is_newer(latest, installed) is False


def test_the_running_version_is_found():
    """It comes from the manifest when NVDA's addonHandler is absent."""
    assert updates.parse_version(updates.installed_version()) is not None


def test_the_tag_page_and_addon_come_back_from_the_payload():
    def opener(url):
        assert url == updates.LATEST_API
        return json.dumps({
            "tag_name": "pantheraspeech/v9.9.9",
            "html_url": "https://example.invalid/releases/tag/x",
            "assets": [
                {"name": "panthera-sapi-9.9.9-setup.exe",
                 "browser_download_url": "https://example.invalid/setup.exe"},
                {"name": "pantheraspeech-9.9.9.nvda-addon",
                 "browser_download_url": "https://example.invalid/a.nvda-addon"},
            ],
        }).encode("utf-8")

    tag, url, addon = updates.latest_release(opener=opener)
    assert tag == "pantheraspeech/v9.9.9"
    assert url == "https://example.invalid/releases/tag/x"
    # The installer exe is not what NVDA installs; the picker must step over
    # it to the .nvda-addon, whatever order GitHub lists them in.
    assert addon == "https://example.invalid/a.nvda-addon"


def test_a_release_without_an_addon_asset_offers_the_page_alone():
    def opener(url):
        return json.dumps({
            "tag_name": "pantheraspeech/v9.9.9",
            "html_url": "https://example.invalid/releases/tag/x",
            "assets": [{"name": "notes.txt",
                        "browser_download_url": "https://example.invalid/n"}],
        }).encode("utf-8")

    tag, url, addon = updates.latest_release(opener=opener)
    assert tag and url
    assert addon is None


def test_a_release_with_no_version_in_it_is_refused():
    """Rather than reported as an update to something unnameable."""
    def opener(url):
        return json.dumps({"tag_name": "nightly"}).encode("utf-8")

    tag, why, addon = updates.latest_release(opener=opener)
    assert tag is None
    assert "version" in why
    assert addon is None


def test_a_network_failure_is_a_reason_and_not_a_traceback():
    def opener(url):
        raise OSError("the network is unreachable")

    tag, why, addon = updates.latest_release(opener=opener)
    assert tag is None
    assert "unreachable" in why
    assert addon is None


def test_nothing_is_fetched_on_a_secure_screen(monkeypatch):
    """The button is hidden there, and this refuses anyway.

    A guard that lives only in the caller is a guard that moves.  On the
    sign-in desktop NVDA is SYSTEM, and reaching onto the network as SYSTEM
    from a dialog nobody could have opened is not a thing this add-on should
    be capable of.
    """
    reached = []

    def opener(url):
        reached.append(url)
        return b"{}"

    monkeypatch.setattr(updates, "_secure", lambda: True)
    tag, why, addon = updates.latest_release(opener=opener)
    assert tag is None
    assert "secure screen" in why
    assert addon is None
    assert not reached
