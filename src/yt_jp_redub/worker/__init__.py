"""Job queue (FIFO, one job at a time) and the per-job pipeline driver.

Subtitles come first: ASR and translation run over the whole video, while the dub
runs chunk by chunk in priority order as soon as a chunk's lines are translated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from typing import Callable

from ..config import Config
from ..db import DB
from ..models import LinesDoc, VideoPaths, Word, atomic_write_json, youtube_id
from ..pipeline import asr, download, ff, hls, segment, subs
from ..pipeline.dub import PaceModel, dub_chunk
from .scheduler import ChunkScheduler
from .storage import enforce_cap

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, cfg: Config, db: DB):
        self.cfg, self.db = cfg, db
        self.sched = ChunkScheduler(cfg.stream.lookahead_chunks)
        self.changed = asyncio.Condition()
        self.progress: dict[str, float] = {}          # fraction of the current stage
        self.current: str | None = None
        self.listeners: list[Callable[[str], None]] = []   # called with video_id on any update
        self.subs_listeners: list[Callable[[str], None]] = []  # called when full subtitles are ready
        self._wake = asyncio.Event()
        self._docs: dict[str, LinesDoc] = {}          # live docs of jobs being processed
        self._disk_docs: dict[str, tuple[float, LinesDoc]] = {}
        self._separator = self._tts = self._translator = None
        self._pace = PaceModel()

    # ------------------------------------------------------------------ public API
    def paths(self, video_id: str) -> VideoPaths:
        return VideoPaths(self.cfg.videos_dir, video_id)

    def submit(self, url: str) -> tuple[str, bool]:
        vid = youtube_id(url)
        if not vid:
            raise ValueError("not a YouTube video URL")
        created = self.db.add_job(vid, f"https://www.youtube.com/watch?v={vid}")
        job = self.db.get_job(vid)
        if job["stage"] == "error":
            self.db.update_job(vid, stage="queued", error=None)
            created = True
        self._wake.set()
        self._notify(vid)
        return vid, created

    def doc(self, video_id: str) -> LinesDoc | None:
        if video_id in self._docs:
            return self._docs[video_id]
        p = self.paths(video_id).lines
        try:
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            return None
        cached = self._disk_docs.get(video_id)
        if cached and cached[0] == mtime:
            return cached[1]
        d = self.paths(video_id).load_lines()
        self._disk_docs[video_id] = (mtime, d)
        return d

    def report_playhead(self, video_id: str, t: float) -> None:
        doc = self.doc(video_id)
        if doc and doc.chunks:
            self.sched.set_playhead(video_id, doc.chunk_at(t))
        self.db.update_job(video_id, last_watched_at=time.time())

    def request_segment(self, video_id: str, chunk: int) -> None:
        self.sched.request(video_id, chunk)

    def status(self, video_id: str) -> dict | None:
        job = self.db.get_job(video_id)
        if not job:
            return None
        doc = self.doc(video_id)
        done = self.db.done_chunks(video_id)
        out = {
            "video_id": video_id, "title": job["title"], "url": job["url"], "duration": job["duration"],
            "stage": job["stage"], "error": job["error"], "stage_progress": self.progress.get(video_id),
            "translated": job["translated"], "total_lines": job["total_lines"],
            "chunks_done": len(done), "chunks_total": len(doc.chunks) if doc else 0,
            "chunks": [[c.start, c.end, c.id in done] for c in doc.chunks] if doc else [],
            "subs_complete": bool(doc is not None and job["translated"] >= job["total_lines"]),
            "ready": bool(doc and (self.paths(video_id).hls / "video.m3u8").exists()),
            "position": self.queue_position(video_id),
        }
        out["percent"] = self.percent(out)
        return out

    def queue_position(self, video_id: str) -> int | None:
        pend = [j["video_id"] for j in self.db.pending_jobs()]
        return pend.index(video_id) if video_id in pend else None

    @staticmethod
    def percent(s: dict) -> int:
        """Rough overall progress for the library grid."""
        stage = s["stage"]
        if stage == "done":
            return 100
        if stage in ("queued", "error"):
            return 0
        frac = s.get("stage_progress") or 0
        if stage == "downloading":
            return int(5 * frac)
        if stage == "transcribing":
            return int(5 + 5 * frac)
        tl = s["total_lines"] or 1
        tr = min(1, s["translated"] / tl)
        ch = s["chunks_done"] / (s["chunks_total"] or 1)
        return int(10 + 10 * tr + 80 * ch)

    async def wait_change(self, timeout: float) -> None:
        async with self.changed:
            try:
                await asyncio.wait_for(self.changed.wait(), timeout)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------ internals
    def _notify(self, vid: str) -> None:
        async def _n():
            async with self.changed:
                self.changed.notify_all()
        asyncio.get_running_loop().create_task(_n())
        for fn in self.listeners:
            try:
                fn(vid)
            except Exception:
                log.exception("listener failed")

    def _set_stage(self, vid: str, stage: str, **kw) -> None:
        self.progress.pop(vid, None)
        self.db.update_job(vid, stage=stage, **kw)
        self._notify(vid)

    def _progress_cb(self, vid: str) -> Callable[[float], None]:
        loop = asyncio.get_running_loop()
        last = [0.0]

        def cb(frac: float) -> None:
            self.progress[vid] = frac
            if time.time() - last[0] > 1.0 or frac >= 1.0:
                last[0] = time.time()
                loop.call_soon_threadsafe(self._notify, vid)
        return cb

    async def run(self) -> None:
        enforce_cap(self.db, self.cfg.videos_dir, self.cfg.storage.cap_gb)
        while True:
            pending = self.db.pending_jobs()
            if not pending:
                self._wake.clear()
                await self._wake.wait()
                continue
            vid = pending[0]["video_id"]
            self.current = vid
            try:
                await self.process(vid)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("job %s failed", vid)
                self._set_stage(vid, "error", error=f"{type(e).__name__}: {e}"[:1000])
            finally:
                self.current = None
            enforce_cap(self.db, self.cfg.videos_dir, self.cfg.storage.cap_gb)

    async def process(self, vid: str, offline: bool = False) -> LinesDoc:
        cfg, paths = self.cfg, self.paths(vid)
        job = self.db.get_job(vid)

        # 1. download
        if not (paths.video.exists() and paths.audio.exists() and paths.info.exists()):
            self._set_stage(vid, "downloading")
            info = await asyncio.to_thread(download.download, job["url"], paths, cfg.download.max_height,
                                           cfg.download.cookies_file, self._progress_cb(vid))
        else:
            info = json.loads(paths.info.read_text(encoding="utf-8"))
        if not paths.audio_wav.exists():
            await asyncio.to_thread(ff.to_wav, paths.audio, paths.audio_wav)
        dur = await asyncio.to_thread(ff.duration, paths.audio_wav)
        self.db.update_job(vid, title=info.get("title"), duration=dur)
        await asyncio.to_thread(hls.prepare_video, paths, cfg.stream.video_segment_sec)

        # 2. ASR over the whole video
        doc = paths.load_lines()
        if doc is None:
            if paths.transcript.exists():
                words = [Word(**w) for w in json.loads(paths.transcript.read_text(encoding="utf-8"))["words"]]
            else:
                self._set_stage(vid, "transcribing")
                words = await asyncio.to_thread(self._transcribe, paths, self._progress_cb(vid))
                atomic_write_json(paths.transcript, {"words": [w.__dict__ for w in words]})
            doc = segment.build(words, dur, cfg.segment)
            paths.save_lines(doc)
        self._docs[vid] = doc
        self.db.set_chunks(vid, [(c.id, c.start, c.end) for c in doc.chunks])
        self.db.update_job(vid, total_lines=len(doc.lines),
                           translated=sum(1 for l in doc.lines if l.ja))
        subs.write_all(doc, paths.subs)
        self._notify(vid)

        # 3. translation (whole video, priority-ordered), dubbing (per chunk) and the
        #    original-audio rendition run together
        if any(not l.ja for l in doc.lines):
            self._set_stage(vid, "translating")
        tasks = [asyncio.create_task(self._translate_all(vid, doc)),
                 asyncio.create_task(asyncio.to_thread(hls.encode_en_segments, paths, doc))]
        try:
            await self._dub_all(vid, doc, tasks[0], offline)
            for t in tasks:
                await t
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
        self._set_stage(vid, "done")
        if not offline:
            self.cleanup(paths)
        self._docs.pop(vid, None)
        return doc

    def _transcribe(self, paths: VideoPaths, progress) -> list[Word]:
        src = paths.audio_wav
        if self.cfg.asr.use_vocals:
            src = paths.root / "vocals_full.wav"
            if not src.exists():
                self._separate_full(paths, src)
        return asr.transcribe(asr.load_audio_16k(src), self.cfg.asr.model, self.cfg.asr.device, progress)

    def _separate_full(self, paths: VideoPaths, out) -> None:
        import numpy as np
        import soundfile as sf
        sep = self._get_separator()
        total = ff.duration(paths.audio_wav)
        with sf.SoundFile(str(out), "w", samplerate=ff.SR, channels=2, subtype="PCM_16") as f:
            t = 0.0
            while t < total:
                data, sr = ff.read_slice(paths.audio_wav, t, min(total, t + 120))
                v, _ = sep.separate(data, sr)
                f.write(np.clip(v, -1, 1))
                t += 120

    def _get_separator(self):
        if self._separator is None:
            from ..pipeline.separate import make_separator
            self._separator = make_separator(self.cfg)
        return self._separator

    def _get_tts(self):
        if self._tts is None:
            from ..pipeline.tts import make_tts
            self._tts = make_tts(self.cfg)
        return self._tts

    def _get_translator(self):
        if self._translator is None:
            from ..pipeline.translate import make_translator
            self._translator = make_translator(self.cfg)
        return self._translator

    async def _translate_all(self, vid: str, doc: LinesDoc) -> None:
        paths, tcfg = self.paths(vid), self.cfg.translate
        if all(l.ja for l in doc.lines):
            self._subs_ready(vid)
            return
        translator = self._get_translator()
        in_flight: set[int] = set()   # line ids being translated right now

        def next_batch() -> list | None:
            """Contiguous untranslated lines, starting in the most urgent chunk that still has any."""
            for c in self.sched.order(vid, len(doc.chunks)):
                first = next((i for i in doc.chunks[c].line_ids
                              if not doc.lines[i].ja and i not in in_flight), None)
                if first is None:
                    continue
                batch, i = [], first
                while (i < len(doc.lines) and len(batch) < tcfg.batch_lines
                       and not doc.lines[i].ja and i not in in_flight):
                    batch.append(doc.lines[i])
                    i += 1
                return batch
            return None

        async def run_batch(batch) -> None:
            first = batch[0].id
            context = doc.lines[max(0, first - tcfg.context_lines): first]
            try:
                result = await translator.translate(batch, context)
                for l in batch:
                    l.ja = result[l.id]
            finally:
                in_flight.difference_update(l.id for l in batch)
            await asyncio.to_thread(self._save_translation, paths, doc)
            self.db.update_job(vid, translated=sum(1 for l in doc.lines if l.ja))
            self._notify(vid)

        # several batches in flight: free-tier models can take a minute per batch
        running: set[asyncio.Task] = set()
        try:
            while True:
                while len(running) < max(1, int(getattr(tcfg, "concurrency", 1))):
                    batch = next_batch()
                    if not batch:
                        break
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
        self._subs_ready(vid)

    def _save_translation(self, paths: VideoPaths, doc: LinesDoc) -> None:
        paths.save_lines(doc)
        subs.write_all(doc, paths.subs)

    def _subs_ready(self, vid: str) -> None:
        for fn in self.subs_listeners:
            try:
                fn(vid)
            except Exception:
                log.exception("subs listener failed")

    async def _dub_all(self, vid: str, doc: LinesDoc, trans: asyncio.Task, offline: bool) -> None:
        paths = self.paths(vid)
        n = len(doc.chunks)
        started = False

        def eligible(c: int) -> bool:
            return all(doc.lines[i].ja for i in doc.chunks[c].line_ids)

        while True:
            db_done = self.db.done_chunks(vid)
            # a chunk is only really done if its segment exists (and, offline, its wav for the .mkv)
            done = {c for c in db_done if paths.seg("ja", c).exists()
                    and (not offline or (paths.chunk_dir(c) / "dub.wav").exists())}
            if len(done) >= n:
                return
            c = self.sched.next(vid, n, done, eligible)
            if c is None:
                if trans.done():
                    trans.result()  # re-raise a translation failure
                    if not any(eligible(x) for x in range(n) if x not in done):
                        raise RuntimeError("no chunk can be dubbed")
                await self.wait_change(5)
                continue
            if not started:
                self._set_stage(vid, "dubbing")
                started = True
            chunk = doc.chunks[c]
            t0 = time.time()
            wav = paths.chunk_dir(c) / "dub.wav"
            if not (c in db_done and wav.exists()):  # otherwise only the HLS segment is missing
                wav = await asyncio.to_thread(dub_chunk, paths, doc, chunk, self._get_separator()
                                              if chunk.line_ids else None, self._get_tts()
                                              if chunk.line_ids else None, self.cfg, self._pace)
            await asyncio.to_thread(hls.encode_segment, wav, paths.seg("ja", c), chunk.start)
            self.db.chunk_done(vid, c)
            log.info("%s chunk %d/%d dubbed in %.1fs (%.1fs audio)", vid, c + 1, n, time.time() - t0, chunk.duration)
            self._notify(vid)

    def cleanup(self, paths: VideoPaths) -> None:
        """Drop the large intermediates once the HLS segments exist."""
        for c in paths.chunks.glob("*"):
            for name in ("tts", "refs"):
                shutil.rmtree(c / name, ignore_errors=True)
            for name in ("vocals.wav", "background.wav", "dub.wav"):
                (c / name).unlink(missing_ok=True)
        paths.audio_wav.unlink(missing_ok=True)
        (paths.root / "vocals_full.wav").unlink(missing_ok=True)

    async def close(self) -> None:
        if self._translator:
            await self._translator.close()
        if self._tts:
            self._tts.close()
