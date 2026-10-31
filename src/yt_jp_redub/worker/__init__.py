"""Job queue (FIFO, one job at a time) and the per-job pipeline driver.

Subtitles come first: ASR and translation run over the whole video, while the dub
runs chunk by chunk in priority order as soon as a chunk's lines are translated.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import logging
import math
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from functools import cached_property
from pathlib import Path

from ..config import Config
from ..db import DB
from ..models import LinesDoc, VideoPaths, Word, atomic_write_json, video_ref
from ..pipeline import asr, download, export, ff, hls, segment, subs
from ..pipeline.pace import PaceModel
from .dubbing import dub_video
from .eta import DubClock
from .scheduler import ChunkScheduler
from .status import job_status
from .storage import enforce_cap
from .translation import translate_video

log = logging.getLogger(__name__)
Listener = Callable[[str], None]
# all the models at once take ~10 GB (ASR 3.6, IndexTTS 4.9, Demucs 1.3) plus what the desktop uses
LOW_VRAM_MIB = 14000


def gpu_memory_mib() -> int | None:
    """The smallest NVIDIA GPU's memory; None without one (Macs, CPU only)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
        return min(int(x) for x in out.split())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


class Models:
    """The heavy backends: loaded when a video starts (preload) or on first use, and freed by
    unload once nothing has needed them for a while."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @cached_property
    def separator(self):
        from ..pipeline.separate import make_separator

        return make_separator(self.cfg)

    @cached_property
    def tts(self):
        from ..pipeline.tts import make_tts

        return make_tts(self.cfg)

    @cached_property
    def translator(self):
        from ..pipeline.translate import make_translator

        return make_translator(self.cfg)

    @cached_property
    def low_vram(self) -> bool:
        """A GPU too small for every model at once: transcription's (ASR) and the dub's (Demucs,
        IndexTTS) then take turns."""
        if self.cfg.server.low_vram != "auto":
            return str(self.cfg.server.low_vram).lower() == "true"
        mib = gpu_memory_mib()
        return mib is not None and mib < LOW_VRAM_MIB

    def preload(self, only: str | None = None) -> None:
        """Loads ASR, Demucs and IndexTTS while a video downloads, so the stages don't wait for them.
        With only="asr" or "dub" (low VRAM) it loads just that stage's, after freeing the other's."""
        try:
            if only:
                self.unload("dub" if only == "asr" else "asr")
            if only != "dub":
                asr.load(self.cfg.asr.model, self.cfg.asr.device)
            if only != "asr":
                self.separator, self.tts  # noqa: B018 (touching the properties loads them)
            log.info("Models loaded%s", f" ({only})" if only else "")
        except Exception as e:
            log.warning("Model preload failed (will retry when needed): %s", e)

    def loaded(self) -> bool:
        return "separator" in self.__dict__ or "tts" in self.__dict__ or asr.loaded()

    def unload(self, stage: str | None = None) -> None:
        """Frees the GPU and memory they hold (only one stage's, "asr" or "dub", with stage=); the
        IndexTTS process exits."""
        if stage != "asr":
            if "tts" in self.__dict__:
                self.__dict__.pop("tts").close()
            self.__dict__.pop("separator", None)
        if stage != "dub":
            asr.unload()
        gc.collect()
        if "torch" in sys.modules:  # Demucs' memory goes back to the GPU, not just to torch's cache
            sys.modules["torch"].cuda.empty_cache()
        log.info("Models unloaded%s", f" ({stage})" if stage else "")

    async def close(self) -> None:
        if "translator" in self.__dict__:
            await self.translator.close()
        if "tts" in self.__dict__:
            self.tts.close()


class Worker:
    def __init__(self, cfg: Config, db: DB):
        self.cfg, self.db = cfg, db
        self.models = Models(cfg)
        self.sched = ChunkScheduler(cfg.stream.lookahead_chunks)
        self.pace = PaceModel()
        self.clock = DubClock()
        self.changed = asyncio.Condition()
        self.progress: dict[str, float] = {}  # fraction of the current stage
        self.current: str | None = None
        self.listeners: list[Listener] = []  # called with video_id on any update
        self.subs_listeners: list[Listener] = []  # called when a video's full subtitles are ready
        self._wake = asyncio.Event()
        self._docs: dict[str, LinesDoc] = {}  # live docs of jobs being processed
        self._disk_docs: dict[str, tuple[float, LinesDoc]] = {}
        self._offline_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: set[asyncio.Task] = set()  # background work, referenced until done
        self._preload: asyncio.Future | None = None

    # ------------------------------------------------------------------ queries
    def paths(self, video_id: str) -> VideoPaths:
        return VideoPaths(self.cfg.videos_dir, video_id)

    def doc(self, video_id: str) -> LinesDoc | None:
        if video_id in self._docs:
            return self._docs[video_id]
        try:
            mtime = self.paths(video_id).lines.stat().st_mtime
        except FileNotFoundError:
            return None
        cached = self._disk_docs.get(video_id)
        if not cached or cached[0] != mtime:
            cached = self._disk_docs[video_id] = (mtime, self.paths(video_id).load_lines())
        return cached[1]

    def status(self, video_id: str) -> dict | None:
        return job_status(self, video_id)

    async def wait_change(self, timeout: float) -> None:
        async with self.changed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), timeout)

    # ------------------------------------------------------------------ commands
    def submit(self, url: str) -> tuple[str, bool]:
        ref = video_ref(url)
        if not ref:
            raise ValueError("not a YouTube video or Twitch VOD/clip URL")
        vid, canonical = ref
        created = self.db.add_job(vid, canonical)
        if self.db.get_job(vid)["stage"] == "error":
            self.db.update_job(vid, stage="queued", error=None)
            created = True
        self._wake.set()
        self.notify(vid)
        return vid, created

    def delete(self, video_id: str) -> None:
        if video_id == self.current:
            raise RuntimeError("video is being processed")
        shutil.rmtree(self.paths(video_id).root, ignore_errors=True)
        self.db.delete_job(video_id)
        self._disk_docs.pop(video_id, None)

    def report_playhead(self, video_id: str, t: float, save: bool = True) -> None:
        """Where the viewer is: a priority hint, and (with save) where the video resumes next time."""
        doc = self.doc(video_id)
        if doc and doc.chunks:
            self.sched.set_playhead(video_id, doc.chunk_at(t))
        fields = {"last_watched_at": time.time()}
        if save:
            fields["resume_at"] = max(0.0, t)
        self.db.update_job(video_id, **fields)

    # ------------------------------------------------------------------ offline download
    async def dub_track(self, vid: str) -> Path:
        """The finished dub as one audio track: made once, when the dub is done (or on first need
        for videos dubbed before this existed), so a download only packages streams."""
        paths = self.paths(vid)
        async with self._offline_locks[vid]:
            if not paths.dub_audio.exists():
                await asyncio.to_thread(export.write_dub_track, paths, self.doc(vid))
        return paths.dub_audio

    async def offline_file(self, vid: str) -> Path:
        """The .mp4 download, packaged on request (a second or two; nothing is re-encoded). Only the
        latest one is kept, so downloads don't double the disk use."""
        paths = self.paths(vid)
        await self.dub_track(vid)
        async with self._offline_locks[vid]:
            if not paths.export.exists() or paths.export.stat().st_mtime < paths.dub_audio.stat().st_mtime:
                title = self.db.get_job(vid)["title"] or vid
                await asyncio.to_thread(export.write_file, paths, self.doc(vid), paths.export, title)
        self._drop_offline_files(keep=paths.export)
        return paths.export

    def _drop_offline_files(self, keep: Path | None = None) -> None:
        for p in self.cfg.videos_dir.glob("*/export.mp4"):
            if p != keep:
                with contextlib.suppress(OSError):  # Windows: still being sent
                    p.unlink()

    def _background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _make_dub_tracks(self, vids: list[str]) -> None:
        for vid in vids:
            try:
                await self.dub_track(vid)
            except Exception:
                log.exception("Joining the dub of %s into one track failed", vid)

    def request_segment(self, video_id: str, chunk: int) -> None:
        self.sched.request(video_id, chunk)

    # ------------------------------------------------------------------ events
    def notify(self, vid: str) -> None:
        async def wake_waiters():
            async with self.changed:
                self.changed.notify_all()

        self._background(wake_waiters())
        _emit(self.listeners, vid)

    def set_stage(self, vid: str, stage: str, **fields) -> None:
        self.progress.pop(vid, None)
        self.db.update_job(vid, stage=stage, **fields)
        self.notify(vid)

    def _progress_cb(self, vid: str) -> Callable[[float], None]:
        """Thread-safe progress reporting, at most once a second."""
        loop, last = asyncio.get_running_loop(), [0.0]

        def cb(frac: float) -> None:
            self.progress[vid] = frac
            if time.time() - last[0] > 1.0 or frac >= 1.0:
                last[0] = time.time()
                loop.call_soon_threadsafe(self.notify, vid)

        return cb

    # ------------------------------------------------------------------ the queue
    async def run(self) -> None:
        await self._enforce_cap()
        # finished videos from before dub tracks existed get one now, so their download is instant
        self._drop_offline_files()
        done = [j["video_id"] for j in self.db.list_jobs() if j["stage"] == "done"]
        self._background(
            self._make_dub_tracks(
                [v for v in done if self.status(v)["ready"] and not self.paths(v).dub_audio.exists()]
            )
        )
        idle = self.cfg.server.unload_after_min * 60
        while True:
            pending = self.db.pending_jobs()
            if not pending:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), idle if idle and self.models.loaded() else None)
                except TimeoutError:
                    if self._preload:  # a load still under way would come back after the unload
                        await self._preload
                    await asyncio.to_thread(self.models.unload)
                continue
            vid = self.current = pending[0]["video_id"]
            # the models load while the video downloads and is transcribed
            only = None
            if self.models.low_vram:  # only the next stage's (the dub's follow in process)
                only = "dub" if self.paths(vid).transcript.exists() else "asr"
            self._preload = asyncio.get_running_loop().run_in_executor(None, self.models.preload, only)
            try:
                await self.process(vid)
                self.cleanup(self.paths(vid))
                self._background(self._make_dub_tracks([vid]))
            except Exception as e:
                log.exception("job %s failed", vid)
                self.set_stage(vid, "error", error=f"{type(e).__name__}: {e}"[:1000])
            finally:
                self.current = None
            await self._enforce_cap()

    async def _enforce_cap(self) -> None:
        await asyncio.to_thread(
            enforce_cap, self.db, self.cfg.videos_dir, self.cfg.storage.cap_gb, self.delete
        )

    async def process(self, vid: str) -> LinesDoc:
        """Runs (or resumes) every stage of one job; each stage skips work already on disk."""
        paths = self.paths(vid)
        for stale in (paths.export, paths.dub_audio):  # they would be stale once the job changes
            stale.unlink(missing_ok=True)
        await self._download(vid, paths)
        doc = await self._transcribe(vid, paths)
        if (
            self.models.low_vram
        ):  # ASR makes way for the dub's models, which load as the first lines translate
            if self._preload:
                await self._preload
            self._preload = asyncio.get_running_loop().run_in_executor(None, self.models.preload, "dub")
        self._docs[vid] = doc
        try:
            self.db.set_chunks(vid, [(c.id, c.start, c.end) for c in doc.chunks])
            self.db.update_job(vid, total_lines=len(doc.lines), translated=sum(1 for l in doc.lines if l.ja))
            subs.write_all(doc, paths.subs)
            self.notify(vid)
            # translation (priority-ordered), dubbing (per chunk) and the original-audio rendition
            # run together
            if any(not l.ja for l in doc.lines):
                self.set_stage(vid, "translating")
            translation = asyncio.create_task(translate_video(self, vid, doc))
            # full subtitles go out (asbplayer) as soon as translation ends, long before the dub does
            translation.add_done_callback(
                lambda t: t.cancelled() or t.exception() or _emit(self.subs_listeners, vid)
            )
            original = asyncio.create_task(asyncio.to_thread(hls.encode_en_segments, paths, doc))
            try:
                await dub_video(self, vid, doc, translation)
                await asyncio.gather(translation, original)
            finally:
                translation.cancel()
                original.cancel()
            self.set_stage(vid, "done")
            return doc
        finally:
            self._docs.pop(vid, None)

    async def _download(self, vid: str, paths: VideoPaths) -> None:
        cfg = self.cfg
        if paths.video.exists() and paths.audio.exists() and paths.info.exists():
            info = json.loads(paths.info.read_text(encoding="utf-8"))
        else:
            self.set_stage(vid, "downloading")
            info = await asyncio.to_thread(
                download.download,
                self.db.get_job(vid)["url"],
                paths,
                cfg.download.max_height,
                cfg.download.cookies_file,
                self._progress_cb(vid),
            )
        if not paths.audio_wav.exists():
            await asyncio.to_thread(ff.to_wav, paths.audio, paths.audio_wav)
        self.db.update_job(
            vid, title=info.get("title"), duration=await asyncio.to_thread(ff.duration, paths.audio_wav)
        )
        await asyncio.to_thread(hls.prepare_video, paths, cfg.stream.video_segment_sec)

    async def _transcribe(self, vid: str, paths: VideoPaths) -> LinesDoc:
        doc = paths.load_lines()
        if doc is not None:
            return doc
        if paths.transcript.exists():
            words = [Word(**w) for w in json.loads(paths.transcript.read_text(encoding="utf-8"))["words"]]
        else:
            self.set_stage(vid, "transcribing")
            words = await asyncio.to_thread(self._asr, paths, self._progress_cb(vid))
            atomic_write_json(paths.transcript, {"words": [w.__dict__ for w in words]})
        doc = segment.build(words, self.db.get_job(vid)["duration"], self.cfg.segment)
        paths.save_lines(doc)
        return doc

    def _asr(self, paths: VideoPaths, progress) -> list[Word]:
        src = paths.audio_wav
        if self.cfg.asr.use_vocals:
            src = paths.root / "vocals_full.wav"
            if not src.exists():
                self._separate_full(paths, src)
        return asr.transcribe(asr.load_audio_16k(src), self.cfg.asr.model, self.cfg.asr.device, progress)

    def _separate_full(self, paths: VideoPaths, out) -> None:
        import numpy as np
        import soundfile as sf

        total = ff.duration(paths.audio_wav)
        with sf.SoundFile(str(out), "w", samplerate=ff.SR, channels=2, subtype="PCM_16") as f:
            for t in range(0, math.ceil(total), 120):
                data, sr = ff.read_slice(paths.audio_wav, t, min(total, t + 120))
                f.write(np.clip(self.models.separator.separate(data, sr)[0], -1, 1))

    @staticmethod
    def cleanup(paths: VideoPaths) -> None:
        """Drops the large intermediates once the HLS segments exist."""
        for c in paths.chunks.glob("*"):
            shutil.rmtree(c, ignore_errors=True)
        paths.audio_wav.unlink(missing_ok=True)
        (paths.root / "vocals_full.wav").unlink(missing_ok=True)

    async def close(self) -> None:
        await self.models.close()


def _emit(listeners: list[Listener], vid: str) -> None:
    for fn in listeners:
        try:
            fn(vid)
        except Exception:
            log.exception("listener failed")
