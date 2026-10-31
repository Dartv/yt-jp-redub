"""Whole-video translation, most urgent chunk first, several batches in flight."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time

from ..models import Line, LinesDoc, VideoPaths
from ..pipeline import subs
from ..pipeline.translate import Request, Retryable, char_budgets

log = logging.getLogger(__name__)
HEAD_BATCH = 12  # chunks about to be watched go out in small batches: latency follows output length
HEAD_RETRY_CAP = 8  # max seconds between attempts at a batch someone is waiting for
RETRY_CAP = 60
GLOSSARY_LINES = 120  # transcript lines the glossary is drawn from


async def _glossary(w, title: str, doc: LinesDoc) -> dict[str, str]:
    """Names for every batch to agree on: from the start of the transcript and a sample of
    the rest. Without it batches translate on their own (a failure here never stops a job)."""
    lines = doc.lines[: GLOSSARY_LINES // 2]
    rest = doc.lines[GLOSSARY_LINES // 2 :]
    lines += rest[:: max(1, math.ceil(len(rest) / (GLOSSARY_LINES // 2)))]
    try:
        return await w.models.translator.glossary(title, lines)
    except Exception as e:
        log.warning("no glossary for %s: %s", title, str(e)[:200])
        return {}


async def translate_video(w, vid: str, doc: LinesDoc) -> None:
    """Fills line.ja for every line, saving lines.json and the subtitle files after each batch.
    The chunks about to be watched go out right away; the rest wait for the glossary."""
    if all(l.ja for l in doc.lines):  # resumed after translation finished: no glossary call either
        return
    paths, cfg = w.paths(vid), w.cfg.translate
    budgets = char_budgets(doc.lines)  # how much Japanese fits each line's timing
    title = w.db.get_job(vid)["title"] or ""  # helps make sense of misheard names and topics
    glossary: asyncio.Task | None = None  # started once the first batches are out, which go first
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
        urgent = w.sched.urgent(vid, len(doc.chunks))
        for c in order:
            todo = [
                i
                for i in doc.chunks[c].line_ids
                if not doc.lines[i].ja and i not in in_flight and cooldown.get(i, 0) <= now
            ]
            if not todo:
                continue
            head = c in urgent
            if not head and not (glossary and glossary.done()):
                return None
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
        first, last = batch[0].id, batch[-1].id + 1
        req = Request(
            batch,
            before=doc.lines[max(0, first - cfg.context_lines) : first],
            after=doc.lines[last : last + cfg.context_lines],
            budgets=budgets,
            title=title,
            glossary=glossary.result() if glossary and glossary.done() else {},
            urgent=head,
            timeout=60 + 3 * len(batch),  # replies take time in proportion to their length
        )
        ok = False
        try:
            result = await w.models.translator.translate(req)
            ok = True
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
            w.clock.translated(batch, ok)
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
                w.clock.translating(*picked)
                running.add(asyncio.create_task(run_batch(*picked)))
            if glossary is None:
                glossary = asyncio.create_task(_glossary(w, title, doc))
            retry_at = next_retry()
            if not running and not glossary.done():  # the rest of the video waits for it
                # but not a seek: a new head chunk goes out without it, so look again every 2 s
                wait = 2.0 if retry_at is None else min(2.0, max(0.05, retry_at - time.monotonic()))
                await asyncio.wait([glossary], timeout=wait)
                continue
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
        if glossary:
            glossary.cancel()
        for t in running:
            t.cancel()


def _save(paths: VideoPaths, doc: LinesDoc) -> None:
    paths.save_lines(doc)
    subs.write_all(doc, paths.subs)
