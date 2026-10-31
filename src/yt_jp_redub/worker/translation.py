"""Whole-video translation, most urgent chunk first, several batches in flight."""

from __future__ import annotations

import asyncio
import logging
import random
import time

from ..models import Line, LinesDoc, VideoPaths
from ..pipeline import subs
from ..pipeline.translate import Retryable, char_budgets

log = logging.getLogger(__name__)
HEAD_BATCH = 12  # chunks about to be watched go out in small batches: latency follows output length
HEAD_RETRY_CAP = 8  # max seconds between attempts at a batch someone is waiting for
RETRY_CAP = 60


async def translate_video(w, vid: str, doc: LinesDoc) -> None:
    """Fills line.ja for every line, saving lines.json and the subtitle files after each batch."""
    paths, cfg = w.paths(vid), w.cfg.translate
    budgets = char_budgets(doc.lines)  # how much Japanese fits each line's timing
    in_flight: set[int] = set()
    cooldown: dict[int, float] = {}  # line id -> when its failed batch may be sent again
    failures: dict[int, int] = {}  # first line id of a failing batch -> failed attempts
    save_lock = asyncio.Lock()  # batches finish together; one writer at a time

    def next_batch() -> tuple[list[Line], bool] | None:
        """Untranslated lines of the most urgent chunk that has any, and whether that chunk is
        about to be watched. Batches never cross chunk boundaries, so a chunk becomes dubbable as
        soon as its own batch returns. A failed batch rejoins the line here after a short pause,
        so it is retried ahead of less urgent chunks."""
        order, now = w.sched.order(vid, len(doc.chunks)), time.monotonic()
        for c in order:
            todo = [
                i
                for i in doc.chunks[c].line_ids
                if not doc.lines[i].ja and i not in in_flight and cooldown.get(i, 0) <= now
            ]
            if not todo:
                continue
            head = c in order[:2]
            size = min(cfg.batch_lines, HEAD_BATCH) if head else cfg.batch_lines
            batch = [doc.lines[todo[0]]]
            for i in todo[1:size]:
                if i != batch[-1].id + 1:
                    break
                batch.append(doc.lines[i])
            return batch, head
        return None

    def next_retry() -> float | None:
        """When the earliest cooling-down batch may go out again."""
        waits = [t for i, t in cooldown.items() if not doc.lines[i].ja and i not in in_flight]
        return min(waits) if waits else None

    async def run_batch(batch: list[Line], head: bool) -> None:
        first = batch[0].id
        try:
            result = await w.models.translator.translate(
                batch,
                doc.lines[max(0, first - cfg.context_lines) : first],
                budgets,
                timeout=60 + 3 * len(batch),  # replies take time in proportion to their length
            )
        except Retryable as e:
            n = failures[first] = failures.get(first, 0) + 1
            if n > cfg.max_retries:
                raise RuntimeError(f"translation failed after {n} attempts: {e}") from e
            delay = min(2.0**n, HEAD_RETRY_CAP if head else RETRY_CAP) + random.random()
            log.warning(
                "translating lines %d-%d failed (attempt %d), retrying in %.0f s: %s",
                first,
                batch[-1].id,
                n,
                delay,
                str(e)[:300],
            )
            cooldown.update((l.id, time.monotonic() + delay) for l in batch)
            return
        finally:
            in_flight.difference_update(l.id for l in batch)
        failures.pop(first, None)
        for l in batch:
            l.ja = result[l.id]
        async with save_lock:
            await asyncio.to_thread(_save, paths, doc)
        w.db.update_job(vid, translated=sum(1 for l in doc.lines if l.ja))
        w.notify(vid)

    running: set[asyncio.Task] = set()
    try:
        while True:
            while len(running) < max(1, cfg.concurrency) and (picked := next_batch()):
                in_flight.update(l.id for l in picked[0])
                running.add(asyncio.create_task(run_batch(*picked)))
            retry_at = next_retry()
            if not running and retry_at is None:
                break
            wait = None if retry_at is None else max(0.05, retry_at - time.monotonic())
            if not running:
                await asyncio.sleep(wait)
                continue
            done, running = await asyncio.wait(running, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                t.result()
    finally:
        for t in running:
            t.cancel()


def _save(paths: VideoPaths, doc: LinesDoc) -> None:
    paths.save_lines(doc)
    subs.write_all(doc, paths.subs)
