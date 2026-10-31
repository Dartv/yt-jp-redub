"""SQLite job and chunk state, so restarts resume where they left off."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  video_id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  title TEXT,
  duration REAL,
  stage TEXT NOT NULL DEFAULT 'queued',
  error TEXT,
  translated INTEGER NOT NULL DEFAULT 0,
  total_lines INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  last_watched_at REAL,
  resume_at REAL,
  size_bytes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chunks (
  video_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  start REAL NOT NULL,
  end REAL NOT NULL,
  done INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (video_id, idx)
);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(jobs)")}
            if "resume_at" not in cols:  # databases from before resume positions
                self._conn.execute("ALTER TABLE jobs ADD COLUMN resume_at REAL")

    def _x(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # jobs
    def add_job(self, video_id: str, url: str) -> bool:
        """Returns True if newly created."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO jobs(video_id,url,created_at,updated_at) VALUES(?,?,?,?)",
                (video_id, url, now, now),
            )
            return cur.rowcount == 1

    def get_job(self, video_id: str) -> dict | None:
        r = self._x("SELECT * FROM jobs WHERE video_id=?", (video_id,))
        return dict(r[0]) if r else None

    def list_jobs(self) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM jobs ORDER BY created_at DESC")]

    def pending_jobs(self) -> list[dict]:
        return [
            dict(r)
            for r in self._x("SELECT * FROM jobs WHERE stage NOT IN ('done','error') ORDER BY created_at")
        ]

    def update_job(self, video_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ",".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE jobs SET {cols} WHERE video_id=?", (*fields.values(), video_id))

    def delete_job(self, video_id: str) -> None:
        self._x("DELETE FROM jobs WHERE video_id=?", (video_id,))
        self._x("DELETE FROM chunks WHERE video_id=?", (video_id,))

    # chunks
    def set_chunks(self, video_id: str, chunks: list[tuple[int, float, float]]) -> None:
        existing = {
            r["idx"]: (r["start"], r["end"])
            for r in self._x("SELECT idx,start,end FROM chunks WHERE video_id=?", (video_id,))
        }
        if existing == {i: (s, e) for i, s, e in chunks}:
            return
        self._x("DELETE FROM chunks WHERE video_id=?", (video_id,))
        with self._lock:
            self._conn.executemany(
                "INSERT INTO chunks(video_id,idx,start,end) VALUES(?,?,?,?)",
                [(video_id, i, s, e) for i, s, e in chunks],
            )

    def chunk_done(self, video_id: str, idx: int) -> None:
        self._x("UPDATE chunks SET done=1 WHERE video_id=? AND idx=?", (video_id, idx))

    def done_chunks(self, video_id: str) -> set[int]:
        return {r["idx"] for r in self._x("SELECT idx FROM chunks WHERE video_id=? AND done=1", (video_id,))}
