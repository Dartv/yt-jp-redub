"""The job status the player, library and bot read."""

from __future__ import annotations


def job_status(w, video_id: str) -> dict | None:
    job = w.db.get_job(video_id)
    if not job:
        return None
    doc = w.doc(video_id)
    done = w.db.done_chunks(video_id)
    pending = [j["video_id"] for j in w.db.pending_jobs()]
    out = {
        "video_id": video_id, "title": job["title"], "url": job["url"], "duration": job["duration"],
        "stage": job["stage"], "error": job["error"], "stage_progress": w.progress.get(video_id),
        "translated": job["translated"], "total_lines": job["total_lines"],
        "chunks_done": len(done), "chunks_total": len(doc.chunks) if doc else 0,
        "chunks": [[c.start, c.end, c.id in done] for c in doc.chunks] if doc else [],
        "subs_complete": doc is not None and job["translated"] >= job["total_lines"],
        "ready": doc is not None and (w.paths(video_id).hls / "video.m3u8").exists(),
        "position": pending.index(video_id) if video_id in pending else None,
        "last_watched_at": job["last_watched_at"], "size_bytes": job["size_bytes"],
    }
    out["percent"] = percent(out)
    return out


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
