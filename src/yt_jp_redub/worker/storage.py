"""Storage cap: delete least recently watched finished videos when over the limit."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def enforce_cap(db, videos_dir: Path, cap_gb: float, delete: Callable[[str], None]) -> list[str]:
    cap = cap_gb * 1024**3
    jobs = db.list_jobs()
    sizes = {}
    for j in jobs:
        sizes[j["video_id"]] = dir_size(videos_dir / j["video_id"])
        db.update_job(j["video_id"], size_bytes=sizes[j["video_id"]])
    total = sum(sizes.values())
    deleted = []
    candidates = sorted(
        (j for j in jobs if j["stage"] in ("done", "error")),
        key=lambda j: j["last_watched_at"] or j["updated_at"] or 0,
    )
    for j in candidates:
        if total <= cap:
            break
        vid = j["video_id"]
        log.info("Storage cap: deleting %s (%s)", vid, j.get("title"))
        delete(vid)
        total -= sizes[vid]
        deleted.append(vid)
    return deleted
