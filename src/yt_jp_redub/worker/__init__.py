"""Job queue (FIFO, one job at a time) and the per-job pipeline driver.

The audio downloads first, and the video while the rest runs. Subtitles come first: ASR and
translation run over the whole video, while the dub runs chunk by chunk in priority order as
soon as a chunk's lines are translated.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import logging
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
from ..pipeline import asr, download, export, ff, hls, segment, separate, subs
from ..pipeline.pace import PaceModel
from .dubbing import dub_video
from .eta import DubClock
from .scheduler import ChunkScheduler
from .status import job_status
from .storage import enforce_cap
from .translation import translate_video

log = logging.getLogger(__name__)
Listener = Callable[[str], None]
# all the models at once take ~11 GB (ASR 3.6, IndexTTS 4.9, Demucs 1.3, CUDA overhead) plus what
# the desktop uses
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
        self._old_translators = []

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

    def preload(self, stage: str) -> None:
        """Loads ASR, Demucs and IndexTTS while a video downloads, so the stages don't wait for them.
        With low VRAM only `stage`'s ("asr" or "dub"), after freeing the other's."""
        try:
            if self.low_vram:
                self.unload("dub" if stage == "asr" else "asr")
            if stage == "asr" or not self.low_vram:
                asr.load(self.cfg.asr.model, self.cfg.asr.device)
            if stage == "dub" or not self.low_vram:
                self.separator, self.tts  # noqa: B018 (touching the properties loads them)
            log.info("Models loaded%s", f" ({stage})" if self.low_vram else "")
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

    def settings_changed(self, names: list[str]) -> None:
        """After the settings dialog: a new translation provider is used from the next batch, and low
        VRAM mode is worked out again for the next video."""
        self.__dict__.pop("low_vram", None)
        if any(n.startswith("llm_") for n in names) and "translator" in self.__dict__:
            self._old_translators.append(self.__dict__.pop("translator"))  # a batch may still be using it

    async def close(self) -> None:
        for t in self._old_translators:
            await t.close()
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
        self._threads: dict[str, set[asyncio.Future]] = defaultdict(set)  # a job's thread work

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

    def ready(self, video_id: str) -> bool:
        """It can be played: its lines and the video's HLS segments exist."""
        return self.doc(video_id) is not None and self.paths(video_id).video_playlist.exists()

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
        if self.db.get_job(vid)["stage"] == "error":  # a retry joins the end of the queue
            self.db.update_job(vid, stage="queued", error=None, created_at=time.time())
            created = True
        self._wake.set()
        self.notify(vid)
        return vid, created

    def delete(self, video_id: str) -> None:
        if video_id == self.current:
            raise RuntimeError("video is being processed")
        root = self.paths(video_id).root
        shutil.rmtree(root, ignore_errors=True)
        if root.exists():  # Windows: a file still open (a phone streaming it) can't be deleted yet
            raise RuntimeError("some of its files are in use; try again in a moment")
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

    async def thread(self, vid: str, fn, *args):
        """asyncio.to_thread for a job's work. A failing job stops waiting for its threads but can't stop
        them, so a retry first waits for them: two of them would write the same files."""
        inner = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        self._threads[vid].add(inner)

        def done(f: asyncio.Future) -> None:
            self._threads[vid].discard(f)
            f.cancelled() or f.exception()  # retrieved: a late failure of an abandoned thread isn't news

        inner.add_done_callback(done)
        return await asyncio.shield(inner)

    def _background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _make_dub_track(self, vid: str) -> None:
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
        self._drop_offline_files()
        while True:
            idle = self.cfg.server.unload_after_min * 60
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
            if self._threads[vid]:  # a retry: the failed run's threads finish first
                await asyncio.wait(self._threads[vid])
            # the models load while the video downloads and is transcribed
            stage = "dub" if self.paths(vid).transcript.exists() else "asr"
            self._preload = asyncio.get_running_loop().run_in_executor(None, self.models.preload, stage)
            try:
                await self.process(vid)
                self.cleanup(self.paths(vid))
                self._background(self._make_dub_track(vid))
            except Exception as e:
                log.exception("job %s failed", vid)
                self.set_stage(vid, "error", error=f"{type(e).__name__}: {e}"[:1000])
                # a crashed IndexTTS process would fail every later job: the next one starts a new one
                if self._preload:
                    await self._preload
                await asyncio.to_thread(self.models.unload, "dub")
            finally:
                self.current = None
            await self._enforce_cap(keep=vid)

    async def _enforce_cap(self, keep: str | None = None) -> None:
        try:
            await asyncio.to_thread(
                enforce_cap, self.db, self.cfg.videos_dir, self.cfg.storage.cap_gb, self.delete, keep
            )
        except Exception:  # never worth stopping the queue for
            log.exception("Enforcing the storage cap failed")

    async def process(self, vid: str) -> LinesDoc:
        """Runs (or resumes) every stage of one job; each stage skips work already on disk. Once the
        audio is in, the video download, the translation (priority-ordered), the original-audio
        rendition and the dub (per chunk) run side by side, and the first failure stops them all."""
        paths = self.paths(vid)
        for stale in (paths.export, paths.dub_audio):  # they would be stale once the job changes
            stale.unlink(missing_ok=True)
        try:
            async with asyncio.TaskGroup() as tg:
                await self._download_audio(vid, paths)
                tg.create_task(self._download_video(vid, paths))
                doc = await self._transcribe(vid, paths)
                # with low VRAM, ASR makes way for the dub's models, which load as the first lines translate
                if self.models.low_vram:
                    if self._preload:
                        await self._preload
                    self._preload = asyncio.get_running_loop().run_in_executor(
                        None, self.models.preload, "dub"
                    )
                self._docs[vid] = doc
                self.db.set_chunks(vid, [(c.id, c.start, c.end) for c in doc.chunks])
                self.db.update_job(
                    vid, total_lines=len(doc.lines), translated=sum(1 for l in doc.lines if l.ja)
                )
                subs.write_all(doc, paths.subs)
                self.notify(vid)
                if any(not l.ja for l in doc.lines):
                    self.set_stage(vid, "translating")
                translation = tg.create_task(self._translate(vid, doc))
                tg.create_task(self.thread(vid, hls.encode_en_segments, paths, doc))
                tg.create_task(dub_video(self, vid, doc, translation))
        except ExceptionGroup as eg:
            raise eg.exceptions[0] from None  # the failure that stopped the rest, for the job's error
        finally:
            self._docs.pop(vid, None)
        self.set_stage(vid, "done")
        return doc

    async def _translate(self, vid: str, doc: LinesDoc) -> None:
        await translate_video(self, vid, doc)
        _emit(self.subs_listeners, vid)  # full subtitles (asbplayer), long before the dub is done

    async def _download_audio(self, vid: str, paths: VideoPaths) -> None:
        """The audio is all transcription and the dub need, so it comes first."""
        if paths.audio.exists() and paths.info.exists():
            info = json.loads(paths.info.read_text(encoding="utf-8"))
        else:
            self.set_stage(vid, "downloading")
            url = self.db.get_job(vid)["url"]
            info = await self.thread(
                vid, download.download_audio, url, paths, self.cfg.download, self._progress_cb(vid)
            )
        if not paths.audio_wav.exists():
            await self.thread(vid, ff.to_wav, paths.audio, paths.audio_wav)
        self.db.update_job(
            vid, title=info.get("title"), duration=await asyncio.to_thread(ff.duration, paths.audio_wav)
        )

    async def _download_video(self, vid: str, paths: VideoPaths) -> None:
        """The picture and its HLS segments; the player starts once they exist."""
        if not paths.video_playlist.exists():
            url = self.db.get_job(vid)["url"]
            await self.thread(vid, download.download_video, url, paths, self.cfg.download)
            await self.thread(vid, hls.prepare_video, paths, self.cfg.stream.video_segment_sec)
        self.notify(vid)

    async def _transcribe(self, vid: str, paths: VideoPaths) -> LinesDoc:
        doc = paths.load_lines()
        if doc is not None:
            return doc
        if paths.transcript.exists():
            words = [Word(**w) for w in json.loads(paths.transcript.read_text(encoding="utf-8"))["words"]]
        else:
            self.set_stage(vid, "transcribing")
            words = await self.thread(vid, self._asr, paths, self._progress_cb(vid))
            atomic_write_json(paths.transcript, {"words": [w.__dict__ for w in words]})
        doc = segment.build(words, self.db.get_job(vid)["duration"], self.cfg.segment)
        paths.save_lines(doc)
        return doc

    def _asr(self, paths: VideoPaths, progress) -> list[Word]:
        src = paths.audio_wav
        if self.cfg.asr.use_vocals:
            src = paths.vocals_full
            if not src.exists():
                separate.separate_full(self.models.separator, paths.audio_wav, src)
        return asr.transcribe(asr.load_audio_16k(src), self.cfg.asr.model, self.cfg.asr.device, progress)

    @staticmethod
    def cleanup(paths: VideoPaths) -> None:
        """Drops the large intermediates once the HLS segments exist."""
        for c in paths.chunks.glob("*"):
            shutil.rmtree(c, ignore_errors=True)
        paths.audio_wav.unlink(missing_ok=True)
        paths.vocals_full.unlink(missing_ok=True)

    async def close(self) -> None:
        await self.models.close()


def _emit(listeners: list[Listener], vid: str) -> None:
    for fn in listeners:
        try:
            fn(vid)
        except Exception:
            log.exception("listener failed")
