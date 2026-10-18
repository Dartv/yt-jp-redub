"""Whole-video translation, most urgent chunk first, several batches in flight."""

from __future__ import annotations

import asyncio

from ..models import Line, LinesDoc, VideoPaths
from ..pipeline import subs
from ..pipeline.translate import char_budgets

HEAD_BATCH = 12  # chunks about to be watched go out in small batches: latency follows output length


async def translate_video(w, vid: str, doc: LinesDoc) -> None:
    """Fills line.ja for every line, saving lines.json and the subtitle files after each batch."""
    paths, cfg = w.paths(vid), w.cfg.translate
    budgets = char_budgets(doc.lines)  # how much Japanese fits each line's timing
    in_flight: set[int] = set()
    save_lock = asyncio.Lock()  # batches finish together; one writer at a time

    def next_batch() -> list[Line] | None:
        """Untranslated lines of the most urgent chunk that has any. Batches never cross chunk
        boundaries, so a chunk becomes dubbable as soon as its own batch returns."""
        order = w.sched.order(vid, len(doc.chunks))
        for c in order:
            todo = [i for i in doc.chunks[c].line_ids if not doc.lines[i].ja and i not in in_flight]
            if not todo:
                continue
            size = min(cfg.batch_lines, HEAD_BATCH) if c in order[:2] else cfg.batch_lines
            batch = [doc.lines[todo[0]]]
            for i in todo[1:size]:
                if i != batch[-1].id + 1:
                    break
                batch.append(doc.lines[i])
            return batch
        return None

    async def run_batch(batch: list[Line]) -> None:
        first = batch[0].id
        try:
            result = await w.models.translator.translate(
                batch, doc.lines[max(0, first - cfg.context_lines) : first], budgets
            )
            for l in batch:
                l.ja = result[l.id]
        finally:
            in_flight.difference_update(l.id for l in batch)
        async with save_lock:
            await asyncio.to_thread(_save, paths, doc)
        w.db.update_job(vid, translated=sum(1 for l in doc.lines if l.ja))
        w.notify(vid)

    running: set[asyncio.Task] = set()
    try:
        while True:
            while len(running) < max(1, cfg.concurrency) and (batch := next_batch()):
                in_flight.update(l.id for l in batch)
                running.add(asyncio.create_task(run_batch(batch)))
            if not running:
                break
            done, running = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                t.result()
    finally:
        for t in running:
            t.cancel()


def _save(paths: VideoPaths, doc: LinesDoc) -> None:
    paths.save_lines(doc)
    subs.write_all(doc, paths.subs)
