"""The job status the player and library read."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import Worker

# each stage as the viewer reads it
STAGE_LABELS = {
    "queued": "Queued",
    "downloading": "Downloading",
    "transcribing": "Transcribing",
    "translating": "Translating",
    "dubbing": "Dubbing",
    "done": "Dub complete",
    "error": "Failed",
}


def job_status(w: Worker, video_id: str) -> dict | None:
    job = w.db.get_job(video_id)
    if not job:
        return None
    doc = w.doc(video_id)
    done = w.db.done_chunks(video_id)
    pending = [j["video_id"] for j in w.db.pending_jobs()]
    # seconds until each chunk is dubbed; only known for the video being dubbed now
    eta = (
        w.clock.ready_in(
            video_id,
            doc,
            done,
            w.sched.order(video_id, len(doc.chunks)),
            w.sched.urgent(video_id, len(doc.chunks)),
        )
        if doc and video_id == w.current
        else {}
    )
    out = {
        "video_id": video_id,
        "title": job["title"],
        "url": job["url"],
        "duration": job["duration"],
        "stage": job["stage"],
        "label": STAGE_LABELS.get(job["stage"], job["stage"]),
        "error": job["error"],
        "error_hint": error_hint(job["error"]),
        "stage_progress": w.progress.get(video_id),
        "translated": job["translated"],
        "total_lines": job["total_lines"],
        "chunks_done": len(done),
        "chunks_total": len(doc.chunks) if doc else 0,
        # [start, end, dubbed, seconds until dubbed (0 when dubbed, None when unknown)]
        "chunks": [
            [c.start, c.end, c.id in done, 0 if c.id in done else _round(eta.get(c.id))] for c in doc.chunks
        ]
        if doc
        else [],
        "subs_complete": doc is not None and job["translated"] >= job["total_lines"],
        "ready": w.ready(video_id),
        "position": pending.index(video_id) if video_id in pending else None,
        "last_watched_at": job["last_watched_at"],
        "resume_at": job["resume_at"],
        "size_bytes": job["size_bytes"],
    }
    out["percent"] = percent(out)
    out["download_size"] = download_size(w.paths(video_id))
    return out


# what a failure means for the viewer, by a phrase in its error: (what happened, what to do)
ERROR_HINTS = (
    (
        "translation api",
        "The translation service refused the request",
        "Check the API key and provider in the library's settings, then retry.",
    ),
    ("translation failed", "The translation service kept failing", "Retry in a few minutes."),
    (
        "confirm you",
        "YouTube asked to confirm you're not a bot",
        "Set a cookies file in the library's settings (see the README), then retry.",
    ),
    ("private video", "The video is private", ""),
    ("video unavailable", "The video isn't available (removed, private or region-locked)", ""),
    (
        "members-only",
        "The video is for channel members only",
        "Set a cookies file in the library's settings (see the README), then retry.",
    ),
    ("out of memory", "The GPU ran out of memory", "Close other programs using the GPU, then retry."),
)


def error_hint(error: str | None) -> list[str] | None:
    if not error:
        return None
    low = error.lower()
    return list(
        next(
            (h[1:] for h in ERROR_HINTS if h[0] in low),
            ("Something went wrong", "Retry; the details say more."),
        )
    )


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def download_size(paths) -> int | None:
    """About how big the offline .mp4 will be (it holds these streams), once its dub track exists."""
    try:
        return sum(p.stat().st_size for p in (paths.video, paths.dub_audio, paths.audio))
    except FileNotFoundError:
        return None


def percent(s: dict) -> int:
    """Rough overall progress: download 5%, ASR 5%, translation 10%, dubbing 80%."""
    stage, frac = s["stage"], s.get("stage_progress") or 0
    if stage == "done":
        return 100
    if stage in ("queued", "error"):
        return 0
    if stage == "downloading":
        return int(5 * frac)
    if stage == "transcribing":
        return int(5 + 5 * frac)
    translated = min(1, s["translated"] / (s["total_lines"] or 1))
    return int(10 + 10 * translated + 80 * s["chunks_done"] / (s["chunks_total"] or 1))
