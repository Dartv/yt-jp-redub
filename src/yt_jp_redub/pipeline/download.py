"""yt-dlp download: H.264 video-only + AAC audio, never re-encoded."""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Callable

import yt_dlp

from ..models import VideoPaths, atomic_write_json

log = logging.getLogger(__name__)


def fetch_info(url: str, cookies_file: str = "") -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False))


def download(url: str, paths: VideoPaths, max_height: int = 1080, cookies_file: str = "",
             progress: Callable[[float], None] | None = None, attempts: int = 3) -> dict:
    """Retries with freshly extracted URLs: YouTube intermittently answers 403 on media URLs."""
    for i in range(attempts):
        try:
            return _download(url, paths, max_height, cookies_file, progress)
        except yt_dlp.utils.DownloadError as e:
            if i == attempts - 1 or "403" not in str(e):
                raise
            log.warning("download got HTTP 403, retrying (%d/%d)", i + 1, attempts - 1)
            time.sleep(3 * (i + 1))
    raise AssertionError


def _download(url: str, paths: VideoPaths, max_height: int = 1080, cookies_file: str = "",
             progress: Callable[[float], None] | None = None) -> dict:
    """Downloads source.mp4 (video only) and source.m4a. Returns the info dict."""
    paths.root.mkdir(parents=True, exist_ok=True)
    tmp = paths.root / "dl"
    tmp.mkdir(exist_ok=True)
    state = {"part": 0}

    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = d.get("downloaded_bytes", 0) / total if total else 0.0
            # video is ~90% of the bytes
            progress(min(1.0, frac * 0.9 if state["part"] == 0 else 0.9 + frac * 0.1))

    base = {
        "quiet": True, "noprogress": True, "no_warnings": True, "noplaylist": True, "progress_hooks": [hook],
        "concurrent_fragment_downloads": 4, "retries": 10, "fragment_retries": 10,
        "paths": {"home": str(tmp)}, "overwrites": False,
    }
    if cookies_file:
        base["cookiefile"] = cookies_file

    info = None
    if not paths.video.exists():
        fmt = (f"bv*[vcodec^=avc1][height<={max_height}][ext=mp4]/"
               f"bv*[vcodec^=avc1][height<={max_height}]/bv*[height<={max_height}][ext=mp4]")
        opts = dict(base, format=fmt, outtmpl={"default": "video.%(ext)s"},
                    writethumbnail=True, writeinfojson=False)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.sanitize_info(ydl.extract_info(url, download=True))
        vfile = next(p for p in tmp.glob("video.*") if p.suffix not in (".jpg", ".webp", ".png", ".part", ".ytdl"))
        shutil.move(vfile, paths.video)
        thumbs = [p for p in tmp.glob("video.*") if p.suffix in (".jpg", ".webp", ".png")]
        if thumbs:
            _to_jpg(thumbs[0], paths.thumb)
    state["part"] = 1
    if not paths.audio.exists():
        opts = dict(base, format="ba[ext=m4a]/ba[acodec^=mp4a]/ba", outtmpl={"default": "audio.%(ext)s"})
        with yt_dlp.YoutubeDL(opts) as ydl:
            ainfo = ydl.sanitize_info(ydl.extract_info(url, download=True))
            info = info or ainfo
        afile = next(p for p in tmp.glob("audio.*") if p.suffix not in (".part", ".ytdl"))
        shutil.move(afile, paths.audio)
    shutil.rmtree(tmp, ignore_errors=True)

    if info is None:
        info = json.loads(paths.info.read_text(encoding="utf-8")) if paths.info.exists() else fetch_info(url, cookies_file)
    slim = {k: info.get(k) for k in ("id", "title", "uploader", "channel", "duration", "webpage_url",
                                     "thumbnail", "upload_date", "was_live")}
    atomic_write_json(paths.info, slim)
    if progress:
        progress(1.0)
    return slim


def _to_jpg(src: Path, dst: Path) -> None:
    if src.suffix == ".jpg":
        shutil.move(src, dst)
        return
    from .ff import run_ffmpeg
    run_ffmpeg(["-i", str(src), "-vf", "scale=480:-2", str(dst)])
