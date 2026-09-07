# -*- coding: utf-8 -*-
"""What went wrong last time, kept where somebody can read it.

**A secure screen is the one place with no way to ask.**  NVDA will not let
you turn debug logging on there, the log it writes belongs to SYSTEM rather
than to the person at the keyboard, and the failure that matters -- a
synthesizer refusing to load -- says nothing beyond "could not load".  Tomi
tried it on two machines and got the same silence from both.

What *is* readable there is the detail field of the Mac OS speech data dialog
under Tools, because a read-only edit control can be reviewed and read back a
line at a time.  So that field is the channel: anything recorded here appears
in it, under the engine report, and can be relayed by somebody who cannot copy
and paste.

**Kept in `globalVars` rather than in this module's own globals**, for the same
reason the reporter registry is: NVDA gives every add-on one `sys.modules`, and
a driver and a global plugin loaded from different folders can end up with two
copies of this file.  One dictionary the whole process shares means the record
made by whichever copy the driver used is the one the dialog finds.

Nothing here raises.  A diagnostic that can fail is a second fault to chase in
the place least able to afford one.
"""
import time
import traceback

#: Deliberately a string key in `globalVars.__dict__`, not an attribute: it
#: cannot collide with anything NVDA defines and needs no import of ours.
_KEY = "_panthera_failures"

#: Enough to see a pattern, few enough that the field stays readable.
_KEEP = 6


def _store():
    try:
        import globalVars
        return globalVars.__dict__.setdefault(_KEY, [])
    except Exception:
        return []


def record(what, exc=None):
    """Remember one failure, with its traceback if there is one.

    `what` says which thing failed, in the words a person would use -- the
    generation and what it was trying to do -- because the reader of this is
    the user, relaying it onwards.
    """
    try:
        entry = {"when": time.strftime("%H:%M:%S"), "what": str(what)}
        if exc is not None:
            entry["why"] = "%s: %s" % (type(exc).__name__, exc)
            try:
                entry["trace"] = "".join(traceback.format_exception(
                    type(exc), exc, exc.__traceback__))
            except Exception:
                entry["trace"] = ""
        store = _store()
        store.append(entry)
        del store[:-_KEEP]
        _alsoToFile(entry)
    except Exception:
        pass


#: Beside the add-on, so that a failure survives the screen it happened on.
#:
#: The dialog is the channel that works without tools, but it is gone the
#: moment NVDA restarts, and a secure screen is a place people leave quickly.
#: A file can be fetched afterwards and read at leisure.
#:
#: **In the add-on's own folder rather than the temp directory**, because on a
#: secure screen the temp directory belongs to SYSTEM and is awkward to reach,
#: while the add-on folder is somewhere the user already knows and can read as
#: themselves.  NVDA runs elevated there, so it can write even under Program
#: Files; where it cannot, nothing is lost that the dialog does not still have.
_LOGNAME = "panthera-diagnostics.log"

#: Small enough to open in anything, large enough for several failures.
_LOGMAX = 64 * 1024


def _alsoToFile(entry):
    try:
        import io
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), _LOGNAME)
        try:
            if os.path.getsize(path) > _LOGMAX:
                os.remove(path)
        except OSError:
            pass
        with io.open(path, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (entry["when"], entry["what"]))
            if entry.get("why"):
                f.write("    %s\n" % entry["why"])
            if entry.get("trace"):
                for line in entry["trace"].splitlines():
                    f.write("    %s\n" % line.rstrip())
            f.write("\n")
    except Exception:
        pass


def lines():
    """-> the recorded failures, as report lines.  Empty when there are none.

    **Empty is the normal case and prints nothing at all.**  A heading with
    nothing under it reads as a fault to somebody scanning for one.
    """
    try:
        store = _store()
        if not store:
            return []
        out = ["Recent failures", ""]
        for entry in store:
            out.append("    %s  %s" % (entry["when"], entry["what"]))
            if entry.get("why"):
                out.append("        %s" % entry["why"])
            for line in (entry.get("trace") or "").splitlines():
                line = line.rstrip()
                if line:
                    out.append("        %s" % line)
            out.append("")
        return out
    except Exception:
        return []
