"""Download: H.264 video + AAC audio through the managed yt-dlp binary, re-encoded only if HEVC."""

from __future__ import annotations

import logging
import re
import shutil
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..models import VideoPaths, atomic_write_json
from . import ff, ytdlp
from .ytdlp import DownloadError

log = logging.getLogger(__name__)
_PCT = re.compile(r"^progress:\s*([\d.]+)%")


def download(
    url: str,
    paths: VideoPaths,
    max_height: int = 1080,
    cookies_file: str = "",
    progress: Callable[[float], None] | None = None,
) -> dict:
    """Downloads source.mp4 (video only), source.m4a and thumb.jpg; returns slim video info.

    Works for any site yt-dlp supports; the app accepts YouTube and Twitch VODs/clips.
    YouTube breaks yt-dlp regularly, so a failure forces a yt-dlp update and one more try;
    intermittent HTTP 403s on media URLs get a few retries with freshly extracted URLs.
    """
    ytdlp.update()  # at most once a day
    updated = False
    for attempt in range(4):
        try:
            return _download(url, paths, max_height, cookies_file, progress)
        except DownloadError as e:
            if not updated:
                log.warning(
                    "download failed (%s); updating yt-dlp and retrying", str(e).splitlines()[-1][:200]
                )
                ytdlp.update(force=True)
                updated = True
            elif "403" in str(e) and attempt < 3:
                log.warning("download got HTTP 403, retrying")
                time.sleep(3 * attempt)
            else:
                raise
    raise AssertionError


def _download(
    url: str, paths: VideoPaths, max_height: int, cookies_file: str, progress: Callable[[float], None] | None
) -> dict:
    paths.root.mkdir(parents=True, exist_ok=True)
    tmp = paths.root / "dl"
    tmp.mkdir(exist_ok=True)
    cookies = ["--cookies", cookies_file] if cookies_file else []
    meta = ytdlp.info(url, cookies)
    slim = {
        k: meta.get(k)
        for k in (
            "id",
            "title",
            "uploader",
            "channel",
            "duration",
            "webpage_url",
            "thumbnail",
            "upload_date",
            "was_live",
        )
    }
    # before the media, so the library card has a picture during a long download; card-sized:
    # YouTube's mqdefault, and Twitch's CDN serves any "-<w>x<h>.jpg" size
    if not paths.thumb.exists():
        if meta.get("extractor_key") == "Youtube":
            _thumbnail(f"https://i.ytimg.com/vi/{slim['id']}/mqdefault.jpg", paths.thumb)
        elif thumb := slim["thumbnail"]:
            _thumbnail(re.sub(r"-\d+x\d+\.jpg$", "-320x180.jpg", thumb), paths.thumb)
            if not paths.thumb.exists():
                _thumbnail(thumb, paths.thumb)

    def fetch(fmt: str, name: str, target: Path, lo: float, hi: float) -> None:
        if target.exists():
            return

        def on_line(line: str) -> None:
            m = _PCT.match(line)
            if m and progress:
                progress(lo + (hi - lo) * float(m.group(1)) / 100)

        p = ytdlp.run(
            [
                "--no-playlist",
                "--retries",
                "10",
                "--fragment-retries",
                "10",
                "-N",
                "4",
                *cookies,
                "-f",
                fmt,
                "-o",
                str(tmp / f"{name}.%(ext)s"),
                "--newline",
                "--progress-template",
                "download:progress:%(progress._percent_str)s",
                url,
            ],
            on_line,
        )
        if p.returncode != 0:
            raise DownloadError(p.stderr.strip()[-800:] or "yt-dlp failed")
        files = [f for f in tmp.glob(f"{name}.*") if f.suffix not in (".part", ".ytdl")]
        if not files:
            raise DownloadError(f"yt-dlp produced no {name} file")
        shutil.move(files[0], target)

    # Twitch streams run for hours at 60 fps: 1080p would be ~3.7 GB an hour, 720p roughly half
    if str(meta.get("extractor_key", "")).startswith("Twitch"):
        max_height = min(max_height, 720)
    # prefer H.264 + AAC so browsers can play the HLS stream through MSE (video is ~90% of the bytes)
    fetch(
        f"bv*[vcodec^=avc1][height<={max_height}][ext=mp4]/bv*[vcodec^=avc1][height<={max_height}]/"
        f"bv*[height<={max_height}][ext=mp4]",
        "video",
        paths.video,
        0.0,
        0.9,
    )
    if not paths.audio.exists() and _has_audio(paths.video):
        # Twitch muxes the audio into every video format: take it from there instead of downloading again
        part = tmp / "audio.m4a"
        ff.run_ffmpeg(["-i", str(paths.video), "-map", "0:a:0", "-c", "copy", str(part)])
        shutil.move(part, paths.audio)
    fetch("ba[ext=m4a]/ba[acodec^=mp4a]/ba", "audio", paths.audio, 0.9, 1.0)
    _no_hevc(paths.video, tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    atomic_write_json(paths.info, slim)
    if progress:
        progress(1.0)
    return slim


def _has_audio(path: Path) -> bool:
    return any(s["codec_type"] == "audio" for s in ff.probe(path)["streams"])


def _no_hevc(video: Path, tmp: Path) -> None:
    """Re-encodes HEVC, which Firefox can't play (Twitch clips come only in HEVC), to H.264, video only."""
    codec = next(s for s in ff.probe(video)["streams"] if s["codec_type"] == "video")["codec_name"]
    if codec != "hevc":
        return
    log.info("re-encoding HEVC video to H.264")
    part = tmp / "video.h264.mp4"
    ff.run_ffmpeg(
        [
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-force_key_frames",
            "expr:gte(t,n_forced*2)",  # a keyframe every 2 s, so HLS can cut segments anywhere
            str(part),
        ]
    )
    shutil.move(part, video)


def _thumbnail(url: str, out: Path) -> None:
    if not url.startswith("https://"):  # from the video's metadata
        return
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            out.write_bytes(r.read())
    except OSError as e:  # the server falls back to YouTube's own URL
        log.debug("thumbnail download failed: %s", e)
