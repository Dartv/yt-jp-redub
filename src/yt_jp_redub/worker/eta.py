"""When each chunk's dub will be ready, so the player can start (and resume) as soon as playback
won't catch up with the dub, instead of after a fixed head start."""

from __future__ import annotations

import time

from ..models import Chunk, Line, LinesDoc


def speech_sec(doc: LinesDoc, chunk: Chunk) -> float:
    return sum(doc.lines[i].end - doc.lines[i].start for i in chunk.line_ids)


class DubClock:
    """Measured dubbing speed (GPU seconds per second of speech), the chunk being dubbed right now,
    and how long translation batches take: urgent ones (no reasoning) and the rest separately,
    since they differ several-fold. Speeds are averaged over recent chunks and batches."""

    def __init__(self, sec_per_speech: float = 0.9, urgent_sec: float = 8.0, other_sec: float = 30.0):
        self.sec_per_speech = sec_per_speech
        self.translate_sec = {True: urgent_sec, False: other_sec}
        self.current: tuple[str, int, float, float] | None = None  # (video, chunk, started, expected sec)
        self.sent: dict[int, tuple[float, bool]] = {}  # line id -> (when its batch went out, urgent)

    def expected(self, doc: LinesDoc, chunk: Chunk) -> float:
        return self.sec_per_speech * speech_sec(doc, chunk) or 1.0  # 1 s for a chunk without speech

    def dubbing(self, vid: str, doc: LinesDoc, chunk: Chunk) -> None:
        self.current = (vid, chunk.id, time.time(), self.expected(doc, chunk))

    def dubbed(self, doc: LinesDoc, chunk: Chunk, wall_sec: float) -> None:
        self.current = None
        if speech := speech_sec(doc, chunk):
            self.sec_per_speech += 0.3 * (wall_sec / speech - self.sec_per_speech)

    def translating(self, batch: list[Line], urgent: bool) -> None:
        now = time.time()
        self.sent.update((l.id, (now, urgent)) for l in batch)

    def translated(self, batch: list[Line], ok: bool) -> None:
        sent, urgent = min(self.sent.pop(l.id, (time.time(), False)) for l in batch)
        if ok:
            self.translate_sec[urgent] += 0.3 * (time.time() - sent - self.translate_sec[urgent])

    def _translated_in(self, doc: LinesDoc, chunk: Chunk, urgent: bool, now: float) -> float:
        """Seconds until the chunk's lines are all translated: a batch already out is due a
        batch's time after it was sent, one still waiting a batch's time from now. A batch that is
        overdue (stuck behind a rate limit, or a slow API) is expected to take half as long again as
        it has already, rather than any moment now."""

        def due(i: int) -> float:
            if i not in self.sent:
                return self.translate_sec[urgent]
            sent, kind = self.sent[i]
            return max(1.0, sent + self.translate_sec[kind] - now, (now - sent) / 2)

        return max((due(i) for i in chunk.line_ids if not doc.lines[i].ja), default=0.0)

    def ready_in(
        self, vid: str, doc: LinesDoc, done: set[int], order: list[int], urgent: set[int]
    ) -> dict[int, float]:
        """Seconds from now until each chunk not yet dubbed is, dubbing one chunk at a time in
        `order` (the scheduler's), after its translation."""
        out, t, now = {}, 0.0, time.time()
        if self.current and self.current[0] == vid and self.current[1] not in done:
            _, c, started, expected = self.current
            t = out[c] = max(1.0, started + expected - now)
        for c in order:
            if c in done or c in out:
                continue
            chunk = doc.chunks[c]
            t = out[c] = max(t, self._translated_in(doc, chunk, c in urgent, now)) + self.expected(doc, chunk)
        return out
