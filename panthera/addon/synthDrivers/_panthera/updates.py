# -*- coding: utf-8 -*-
"""Is there a newer release than the one running?

Asked for by Sean, who wanted a check-for-updates button rather than having
to watch the repository.

**Only ever when somebody presses the button.**  Nothing here runs on a timer
and nothing runs at start-up.  A screen reader that quietly contacts a server
every launch is telling that server when its owner sits down at their machine,
and nobody asked it to.  The button is the consent.

**And never on a secure screen.**  There NVDA is SYSTEM on the sign-in
desktop; reaching onto the network as SYSTEM, from a dialog nobody can be
logged in to have opened, is not a thing this add-on should be able to do.
The caller hides the button there; this module refuses as well, because a
guard that lives only in the caller is a guard that moves.

The version comparison is pure and the fetch is not, so the comparison is
tested and the fetch is kept to the smallest piece of code that can be wrong.
"""
import os
import re

#: The recent releases, newest first.  **Not `/releases/latest`**, and the
#: version does **not** come from the tag.
#:
#: Only one release can be the "latest" one, and a release carries assets for
#: four things that do not move together: the add-on, the SAPI installer, the
#: APK and the Linux tarballs.  Reading the version off the tag meant that a
#: release whose tag was newer than its add-on announced an add-on update that
#: did not exist -- offer it and the updater hands over the file it already
#: has, then offers it again forever, because what is installed never catches
#: up with the tag.  The old answer was to leave such a release unmarked, which
#: left the newest release findable only by knowing it was there.
#:
#: So each thing is versioned by **its own asset's filename**, and the newest
#: such asset across these releases wins.  `pantheraspeech-3.3.0.nvda-addon`
#: says 3.3.0 wherever it hangs, so a release may carry an older add-on for
#: people who want one without ever claiming to be an upgrade of it.
#:
#: Drafts and prereleases are skipped here rather than by the endpoint: a draft
#: under test must never advertise itself to everybody.
RELEASES_API = ("https://api.github.com/repos/tgeczy/panthera-speech"
                "/releases?per_page=20")

#: Where to send somebody who wants it.  The human page, not the API.
LATEST_PAGE = "https://github.com/tgeczy/panthera-speech/releases"

#: What the add-on's own asset is called.  Pinned to the shape rather than to
#: `.nvda-addon` alone, so that a second add-on ever attached to a release --
#: a sibling project's, a one-off build -- is not mistaken for this one.
ADDON_ASSET = re.compile(r"^pantheraspeech-\d[\d.]*\.nvda-addon$", re.I)

#: Tags are `pantheraspeech/v2.0.0`; older ones and hand-typed ones may be
#: `v2.0.0` or `2.0.0`.  Anything after the numbers -- `-rc1`, `-beta` -- is
#: matched but not compared, because a prerelease is not offered by this
#: endpoint anyway and a suffix should never make a version look *newer*.
_VERSION = re.compile(r"(\d+(?:\.\d+)*)")


def parse_version(text):
    """-> a tuple of ints, or None if there is no version in `text`.

    Trailing zeroes are not trimmed: (2, 0) and (2, 0, 0) compare equal
    anyway once padded, and padding is done in `is_newer` where both sides
    are known.
    """
    if not text:
        return None
    found = _VERSION.search(str(text))
    if not found:
        return None
    try:
        return tuple(int(part) for part in found.group(1).split("."))
    except ValueError:
        return None


def is_newer(latest, installed):
    """-> True when `latest` is a strictly higher version than `installed`.

    Padded to the same length so 2.1 beats 2.0.9 and 2.0 ties 2.0.0.  Either
    side unparseable is False: an add-on that cannot tell should say nothing
    rather than announce an update that may not exist.
    """
    a, b = parse_version(latest), parse_version(installed)
    if not a or not b:
        return False
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    return a > b


def installed_version():
    """-> the running add-on's version, or None.

    `addonHandler` is the right answer inside NVDA and absent everywhere else,
    so the manifest beside this file is the fallback -- and the manifest is
    what `addonHandler` would have read anyway.
    """
    try:
        import addonHandler
        addon = addonHandler.getCodeAddon()
        if addon and addon.version:
            return addon.version
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    manifest = os.path.join(os.path.dirname(os.path.dirname(here)),
                            "manifest.ini")
    try:
        with open(manifest, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("version"):
                    return line.split("=", 1)[1].strip()
    except (OSError, IndexError):
        pass
    return None


def _secure():
    """-> True on a secure screen, and True if we cannot tell."""
    try:
        import globalVars
        return bool(globalVars.appArgs.secure)
    except Exception:
        # Outside NVDA entirely -- the tests, a command line -- which is not a
        # secure screen.  Only an *NVDA* that will not answer is treated as
        # one, and there is no such thing.
        return False


def newest_addon(releases):
    """-> (versionString, pageUrl, addonUrl) for the newest add-on asset.

    `releases` is GitHub's list, newest release first.  The answer is the
    highest version found in an add-on asset's **filename**, with the page of
    the release that carries it -- and when the same add-on hangs on more than
    one release, the newest of those, because that is where somebody following
    the link expects to land.

    -> (None, None, None) when no release carries an add-on this recognises.
    """
    best = None
    for release in releases or []:
        if release.get("draft") or release.get("prerelease"):
            continue
        page = release.get("html_url") or LATEST_PAGE
        for asset in release.get("assets") or []:
            name = asset.get("name") or ""
            url = asset.get("browser_download_url")
            if not url or not ADDON_ASSET.match(name):
                continue
            version = parse_version(name)
            if version is None:
                continue
            #: Newest first in the list, so `>` and not `>=`: the first
            #: release carrying a given add-on keeps the page link.
            if best is None or version > best[0]:
                best = (version, name, page, url)
            break
    if best is None:
        return None, None, None
    return ".".join(str(n) for n in best[0]), best[2], best[3]


def latest_release(timeout=10, opener=None):
    """-> (versionString, pageUrl, addonUrl) -- or (None, reason, None).

    The version is the one in the add-on asset's own filename, never the
    release tag; see `RELEASES_API` for why that distinction is the whole
    point of this function.

    `addonUrl` is the `.nvda-addon` asset itself, when a release carries
    exactly the file NVDA installs; the caller downloads it and hands it to
    NVDA, whose own install dialog is still the thing that asks.  Sending
    somebody to a web page to find the right link themselves is homework a
    screen reader user did not ask for -- Tomi's words were less polite --
    so the page URL is only the fallback for a release with no such asset.

    `opener` exists for the tests: anything callable that takes a URL and
    returns bytes.  The default reaches the network, which is why it is the
    only part of this module that is not tested.
    """
    if _secure():
        return None, "not while NVDA is on a secure screen", None
    try:
        import json
        if opener is None:
            import urllib.request

            def opener(url):
                request = urllib.request.Request(url, headers={
                    # GitHub refuses an unidentified caller, and this says
                    # who we are without saying anything about the machine.
                    "User-Agent": "panthera-speech-addon",
                    "Accept": "application/vnd.github+json",
                })
                with urllib.request.urlopen(request, timeout=timeout) as r:
                    return r.read()

        payload = json.loads(opener(RELEASES_API).decode("utf-8"))
    except Exception as e:
        return None, (str(e) or e.__class__.__name__), None
    #: A single release object, should the endpoint ever be pointed back at
    #: `/releases/latest`: treated as a list of one rather than crashing.
    if isinstance(payload, dict):
        payload = [payload]
    version, page, addon = newest_addon(payload)
    if version is None:
        return None, "no release offers an add-on to update to", None
    return version, page, addon


def fetch(url, timeout=60):
    """Download `url` into %TEMP% and -> the file's path.

    The old file of the same name goes first, so a half-written download
    from a failed attempt is never the thing NVDA is handed.  Exceptions
    propagate: the caller is on a worker thread and turns them into words.
    """
    import tempfile
    import urllib.request

    name = url.rsplit("/", 1)[-1]
    if not name.endswith(".nvda-addon"):
        name = "panthera-update.nvda-addon"
    path = os.path.join(tempfile.gettempdir(), name)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
    request = urllib.request.Request(url, headers={
        "User-Agent": "panthera-speech-addon"})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        data = r.read()
    with open(path, "wb") as f:
        f.write(data)
    return path
