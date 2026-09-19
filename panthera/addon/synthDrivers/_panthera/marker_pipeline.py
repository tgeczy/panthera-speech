# -*- coding: utf-8 -*-
"""Playback actions inside one engine utterance, with joining disabled.

The engine supplies positions, never the earcon pause itself. Inserting the
pause in PCM preserves every speech sample and the surrounding prosody.
"""
from logHandler import log

from .audio import _silence
from .text import COMMAND_RE


def marker_plan(items):
    """Return text, head actions, interior actions by sync ID, tail actions.

    Join text exactly like _joinFragments before inserting commands. Strip
    document commands across fragment boundaries, then map action positions
    through those deletions. Only our generated syncs reach the command parser.
    A tail sync can disappear or change Lion's prosody; keep tails external.
    """
    text, points = "", []
    for kind, value in items:
        if kind == "text":
            if not value:
                continue
            if text and value[:1].strip() and text[-1:].strip():
                text += " "
            text += value
        else:
            points.append((len(text), (kind, value)))
    spans = [m.span() for m in COMMAND_RE.finditer(text)]
    plain = COMMAND_RE.sub("", text)
    heads, tails, groups = [], [], []
    for position, action in points:
        position -= sum(max(0, min(position, end) - start) for start, end in spans)
        if not plain[:position].strip():
            heads.append(action)
        elif not plain[position:].strip():
            tails.append(action)
        elif groups and groups[-1][0] == position:
            groups[-1][1].append(action)
        else:
            groups.append((position, [action]))
    parts, interior, previous = [], {}, 0
    for ident, (position, actions) in enumerate(groups, 1):
        parts.extend((plain[previous:position], "[[sync 0x%x]]" % ident))
        interior[ident] = actions
        previous = position
    parts.append(plain[previous:])
    return "".join(parts), heads, interior, tails


class MarkerPipelineMixin(object):
    def _flushMarked(self, run, wpm, voice, adj, epoch, vol=0):
        items = list(run)
        del run[:]
        if not items:
            return
        # A previous segment can have discovered an older host in this same
        # speech sequence. Fall back per segment, never repeat earlier audio.
        if not self._markerStreaming:
            self._flushMarkerFallback(items, wpm, voice, adj, epoch, vol)
            return
        text, heads, interior, tails = marker_plan(items)
        started, fed, reached = False, False, set()

        def alive():
            return not self._stopped and self._epoch == epoch

        def actions(values):
            for kind, value in values:
                if not alive():
                    return
                if kind == "index":
                    self._audioQueue.put(("mark", value, None))
                elif kind == "break":
                    self._audioQueue.put(("audio", _silence(value), epoch))

        def start():
            nonlocal started
            if not started:
                actions(heads)
                started = True

        def sink(chunk):
            nonlocal fed
            if not alive():
                return False
            start()
            fed = True
            self._audioQueue.put(("audio", chunk, epoch))
            return True

        def marker(ident, frame):
            if not alive():
                return False
            start()
            actions(interior[ident])
            reached.add(ident)
            return True

        if not text.strip():
            start()
            actions(tails)
            return
        kwargs = dict(sink=sink, volume=vol)
        if interior:
            kwargs.update(markerSink=marker, markerIds=tuple(interior))
        pcm = self._render(text, wpm, voice, self._pitchOffset(adj), **kwargs)
        if pcm is None and not started and alive():
            # Unsupported protocol, or retirement before any response. The
            # legacy path retries and preserves speech on older bundled hosts.
            self._flushMarkerFallback(items, wpm, voice, adj, epoch, vol)
            return
        if alive():
            start()
            if pcm is None:
                # Never replay speech already heard. Release outstanding NVDA
                # indexes at the played boundary so its queue cannot wedge.
                for ident, values in interior.items():
                    if ident not in reached:
                        actions([v for v in values if v[0] == "index"])
                log.debugWarning("%s: interior marker render failed" % self.name)
            actions(tails)
            if pcm is not None and fed:
                gap = self.PAUSE_MS.get(self._pauseMode, 0)
                if gap:
                    self._audioQueue.put(("audio", _silence(gap), epoch))

    def _flushMarkerFallback(self, items, wpm, voice, adj, epoch, vol):
        run, pending, trailing = [], [], []
        for kind, value in items:
            if self._stopped or self._epoch != epoch:
                return
            if kind == "text":
                pending.extend(trailing)
                trailing.clear()
                run.append(value)
            elif kind == "index":
                (trailing if run else pending).append(value)
            elif kind == "break":
                self._flush(run, wpm, voice, adj, epoch, pending, vol, trailing)
                if self._epoch == epoch:
                    self._audioQueue.put(("audio", _silence(value), epoch))
        if not self._stopped and self._epoch == epoch:
            self._flush(run, wpm, voice, adj, epoch, pending, vol, trailing)
