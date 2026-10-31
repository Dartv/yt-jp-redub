"""Chunk-by-chunk dubbing in priority order, as translation makes chunks available."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ..models import Chunk, LinesDoc
from ..pipeline import hls
from ..pipeline.dub import dub_chunk

if TYPE_CHECKING:
    from . import Worker

log = logging.getLogger(__name__)
HEAD_WAIT = 120  # seconds the GPU may idle waiting for the playhead chunk's translation


async def dub_video(w: Worker, vid: str, doc: LinesDoc, translation: asyncio.Task) -> None:
    """Returns once every chunk has its ja HLS segment. Runs beside the translation in one task
    group, so a translation failure stops it too."""
    paths, n = w.paths(vid), len(doc.chunks)
    waiting: tuple[int, float] | None = None  # (chunk, since) while holding out for the playhead chunk

    def translated(c: int) -> bool:
        return all(doc.lines[i].ja for i in doc.chunks[c].line_ids)

    def dub(chunk: Chunk, speech: bool):
        # runs in a thread: the first model lookup may wait for the model to load
        models = (w.models.separator, w.models.tts) if speech else (None, None)
        return dub_chunk(paths, doc, chunk, *models, w.cfg, w.pace)

    while True:
        done = {c for c in w.db.done_chunks(vid) if paths.seg("ja", c).exists()}
        if len(done) >= n:
            return
        urgent = w.sched.next(vid, n, done, lambda _: True)
        c = w.sched.next(vid, n, done, translated)
        if c != urgent and not translation.done():
            # the playhead chunk is still being translated: hold out for it (bounded) rather
            # than tie up the GPU with a chunk far ahead
            if waiting is None or waiting[0] != urgent:
                waiting = (urgent, time.time())
            if c is None or time.time() - waiting[1] < HEAD_WAIT:
                await w.wait_change(2)
                continue
        if c is None:  # translation has finished, yet nothing is dubbable
            raise RuntimeError("no chunk can be dubbed")
        if w.db.get_job(vid)["stage"] != "dubbing":
            w.set_stage(vid, "dubbing")
        chunk, t0 = doc.chunks[c], time.time()
        speech = bool(chunk.line_ids)
        w.clock.dubbing(vid, doc, chunk)
        wav = await w.thread(vid, dub, chunk, speech)
        await w.thread(vid, hls.encode_segment, wav, paths.seg("ja", c), chunk.start)
        w.db.chunk_done(vid, c)
        w.clock.dubbed(doc, chunk, time.time() - t0)
        log.info(
            "%s chunk %d/%d dubbed in %.1fs (%.1fs audio)", vid, c + 1, n, time.time() - t0, chunk.duration
        )
        w.notify(vid)
