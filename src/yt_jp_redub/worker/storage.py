"""Storage cap: delete least recently watched finished videos when over the limit."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)


def dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        with contextlib.suppress(OSError):  # files come and go meanwhile (dub tracks, downloads)
            total += f.stat().st_size if f.is_file() else 0
    return total


def enforce_cap(
    db, videos_dir: Path, cap_gb: float, delete: Callable[[str], None], keep: str | None = None
) -> list[str]:
    """keep: the video that just finished, which the cap never deletes."""
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
        if vid == keep or (db.get_job(vid) or {}).get("stage") not in ("done", "error"):
            continue  # just finished, or retried since the list was read
        log.info("Storage cap: deleting %s (%s)", vid, j.get("title"))
        try:
            delete(vid)
        except RuntimeError as e:
            log.info("Storage cap: %s stays for now: %s", vid, e)
            continue
        total -= sizes[vid]
        deleted.append(vid)
    return deleted
