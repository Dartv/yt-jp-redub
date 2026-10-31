"""Download: AAC audio first, then H.264 video, through the managed yt-dlp binary; HEVC is re-encoded."""

from __future__ import annotations

import logging
import re
import shutil
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..models import VideoPaths, atomic_write_json, is_youtube
from . import ff, ytdlp
from .ytdlp import DownloadError

log = logging.getLogger(__name__)
_PCT = re.compile(r"^progress:\s*([\d.]+)%")


def download_audio(url: str, paths: VideoPaths, cfg, progress: Callable[[float], None] | None = None) -> dict:
    """thumb.jpg, source.m4a and info.json: all that transcription and the dub need, so they can start
    while download_video fetches the picture. Returns slim video info. cfg is the [download] config."""
    return _retrying(_audio, url, paths, cfg, progress)


def download_video(url: str, paths: VideoPaths, cfg) -> None:
    """source.mp4: video only, H.264."""
    _retrying(_video, url, paths, cfg)


def _retrying(fn, *args):
    """Works for any site yt-dlp supports; the app accepts YouTube and Twitch VODs/clips.
    YouTube breaks yt-dlp regularly, so a failure forces a yt-dlp update and one more try;
    intermittent HTTP 403s on media URLs get a few retries with freshly extracted URLs."""
    ytdlp.update()  # at most once a day
    updated = False
    for attempt in range(4):
        try:
            return fn(*args)
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


def _cookies(cfg) -> list[str]:
    return ["--cookies", cfg.cookies_file] if cfg.cookies_file else []


def _fetch(url: str, cfg, fmt: str, target: Path, progress: Callable[[float], None] | None = None) -> None:
    """Downloads format `fmt` to target (skipped when it exists), through a dl/ scratch folder."""
    if target.exists():
        return
    tmp = target.parent / "dl"
    tmp.mkdir(parents=True, exist_ok=True)

    def on_line(line: str) -> None:
        m = _PCT.match(line)
        if m and progress:
            progress(float(m.group(1)) / 100)

    name = target.stem
    p = ytdlp.run(
        [
            "--no-playlist",
            "--retries",
            "10",
            "--fragment-retries",
            "10",
            "-N",
            "4",
            *_cookies(cfg),
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
    shutil.rmtree(tmp, ignore_errors=True)


def _video(url: str, paths: VideoPaths, cfg, progress: Callable[[float], None] | None = None) -> None:
    # Twitch streams run for hours at 60 fps: 1080p would be ~3.7 GB an hour, 720p roughly half
    h = cfg.max_height if is_youtube(paths.id) else min(cfg.max_height, 720)
    # H.264 so browsers can play the HLS stream through MSE
    fmt = f"bv*[vcodec^=avc1][height<={h}][ext=mp4]/bv*[vcodec^=avc1][height<={h}]/bv*[height<={h}][ext=mp4]"
    _fetch(url, cfg, fmt, paths.video, progress)
    _no_hevc(paths.video)


def _audio(url: str, paths: VideoPaths, cfg, progress: Callable[[float], None] | None) -> dict:
    paths.root.mkdir(parents=True, exist_ok=True)
    meta = ytdlp.info(url, _cookies(cfg))
    keys = (
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
    slim = {k: meta.get(k) for k in keys}
    # before the media, so the library card has a picture during a long download; card-sized:
    # YouTube's mqdefault, and Twitch's CDN serves any "-<w>x<h>.jpg" size
    if not paths.thumb.exists():
        if is_youtube(paths.id):
            _thumbnail(f"https://i.ytimg.com/vi/{slim['id']}/mqdefault.jpg", paths.thumb)
        elif thumb := slim["thumbnail"]:
            _thumbnail(re.sub(r"-\d+x\d+\.jpg$", "-320x180.jpg", thumb), paths.thumb)
            if not paths.thumb.exists():
                _thumbnail(thumb, paths.thumb)
    try:
        _fetch(url, cfg, "ba[ext=m4a]/ba[acodec^=mp4a]/ba", paths.audio, progress)
    except DownloadError as e:
        if "format is not available" not in str(e):
            raise
        # Twitch clips have no audio-only format: the audio comes out of the video
        _video(url, paths, cfg, progress)
        if not ff.stream(paths.video, "audio"):
            raise
        part = paths.root / "audio.part.m4a"
        ff.run_ffmpeg(["-i", str(paths.video), "-map", "0:a:0", "-c", "copy", str(part)])
        part.replace(paths.audio)
    atomic_write_json(paths.info, slim)
    if progress:
        progress(1.0)
    return slim


def _no_hevc(video: Path) -> None:
    """Re-encodes HEVC, which Firefox can't play (Twitch clips come only in HEVC), to H.264, video only."""
    if ff.stream(video, "video")["codec_name"] != "hevc":
        return
    log.info("re-encoding HEVC video to H.264")
    part = video.with_suffix(".h264.mp4")
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
    part.replace(video)


def _thumbnail(url: str, out: Path) -> None:
    if not url.startswith("https://"):  # from the video's metadata
        return
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            out.write_bytes(r.read())
    except OSError as e:  # the server falls back to YouTube's own URL
        log.debug("thumbnail download failed: %s", e)
