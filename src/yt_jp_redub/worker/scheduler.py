"""Chunk priority: playhead chunk and the ones after it first, then the rest from the start."""

from __future__ import annotations

import time

PLAYHEAD_TTL = 30 * 60   # a reported playhead stays relevant this long
REQUEST_TTL = 60         # a segment request is a priority hint for this long


class ChunkScheduler:
    def __init__(self, lookahead: int = 3):
        self.lookahead = lookahead
        self.playhead: dict[str, tuple[int, float]] = {}     # video_id -> (chunk, when)
        self.requests: dict[str, dict[int, float]] = {}      # video_id -> {chunk: when}

    def set_playhead(self, video_id: str, chunk: int) -> None:
        self.playhead[video_id] = (chunk, time.time())

    def request(self, video_id: str, chunk: int) -> None:
        self.requests.setdefault(video_id, {})[chunk] = time.time()

    def order(self, video_id: str, n_chunks: int) -> list[int]:
        """All chunk ids, most urgent first."""
        now = time.time()
        ph = self.playhead.get(video_id)
        start = ph[0] if ph and now - ph[1] < PLAYHEAD_TTL else 0
        start = min(max(0, start), max(0, n_chunks - 1))
        base = list(range(start, n_chunks)) + list(range(0, start))
        head = base[: self.lookahead + 1]
        reqs = sorted((c for c, t in self.requests.get(video_id, {}).items()
                       if now - t < REQUEST_TTL and 0 <= c < n_chunks and c not in head),
                      key=lambda c: -self.requests[video_id][c])
        seen, out = set(), []
        for c in head + reqs + base:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def next(self, video_id: str, n_chunks: int, done: set[int], eligible) -> int | None:
        for c in self.order(video_id, n_chunks):
            if c not in done and eligible(c):
                return c
        return None
