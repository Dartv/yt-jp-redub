"""Shared data contracts: per-video file layout, subtitle lines and dub chunks."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|live/|shorts/|embed/|v/)|youtu\.be/|youtube\.com/.*[?&]v=)([A-Za-z0-9_-]{11})"
)

STAGES = ("queued", "downloading", "transcribing", "translating", "dubbing", "done", "error")


def youtube_id(url: str) -> str | None:
    m = YT_ID_RE.search(url)
    return m.group(1) if m else None


def find_youtube_urls(text: str) -> list[str]:
    seen, out = set(), []
    for m in YT_ID_RE.finditer(text):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(f"https://www.youtube.com/watch?v={m.group(1)}")
    return out


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Line:
    id: int
    start: float
    end: float
    en: str
    ja: str | None = None
    chunk: int = 0


@dataclass
class Chunk:
    id: int
    start: float
    end: float
    line_ids: list[int] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class LinesDoc:
    duration: float
    lines: list[Line]
    chunks: list[Chunk]

    def to_json(self) -> dict:
        return {"duration": self.duration, "lines": [asdict(l) for l in self.lines],
                "chunks": [asdict(c) for c in self.chunks]}

    @classmethod
    def from_json(cls, d: dict) -> "LinesDoc":
        return cls(d["duration"], [Line(**l) for l in d["lines"]], [Chunk(**c) for c in d["chunks"]])

    def chunk_at(self, t: float) -> int:
        for c in self.chunks:
            if c.start <= t < c.end:
                return c.id
        return self.chunks[-1].id if self.chunks else 0


def atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: a reader has the file open for a moment
            if attempt == 19:
                raise
            time.sleep(0.05)


class VideoPaths:
    """File layout of data/videos/<youtube_id>/."""

    def __init__(self, videos_dir: Path, video_id: str):
        self.id = video_id
        self.root = videos_dir / video_id

    video = property(lambda s: s.root / "source.mp4")          # video-only, H.264
    audio = property(lambda s: s.root / "source.m4a")          # original audio, untouched
    audio_wav = property(lambda s: s.root / "source.wav")      # 44.1 kHz stereo PCM of the original
    info = property(lambda s: s.root / "info.json")
    thumb = property(lambda s: s.root / "thumb.jpg")
    transcript = property(lambda s: s.root / "transcript.json")
    lines = property(lambda s: s.root / "lines.json")
    subs = property(lambda s: s.root / "subs")
    chunks = property(lambda s: s.root / "chunks")
    hls = property(lambda s: s.root / "hls")

    def chunk_dir(self, n: int) -> Path:
        return self.chunks / f"{n:04d}"

    def seg(self, lang: str, n: int) -> Path:
        return self.hls / lang / f"{n:04d}.aac"

    def load_lines(self) -> LinesDoc | None:
        if not self.lines.exists():
            return None
        return LinesDoc.from_json(json.loads(self.lines.read_text(encoding="utf-8")))

    def save_lines(self, doc: LinesDoc) -> None:
        atomic_write_json(self.lines, doc.to_json())
