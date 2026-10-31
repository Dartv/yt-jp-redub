"""Offline output: one file with ja/en audio and ja/en subtitles, built from the HLS segments.

.mkv for the command line; .mp4 (mov_text subtitles) for phones, whose players don't take .mkv.
The Japanese dub is encoded once into one track (dub.m4a) when the video is done, so building a
file afterwards only packages existing streams and takes a second or two.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from ..models import LinesDoc, VideoPaths
from . import ff
from .hls import AAC_PRIMING


def concat_dub(paths: VideoPaths, doc: LinesDoc, out: Path) -> None:
    """Joins the ja segments sample-accurately: each loses its encoder priming and is fitted
    to its chunk's exact length, so the dub can't drift against the video."""
    with sf.SoundFile(str(out), "w", samplerate=ff.SR, channels=2, subtype="PCM_16") as f:
        for c in doc.chunks:
            data = ff.decode_pcm(paths.seg("ja", c.id))[AAC_PRIMING:]
            want = round(c.end * ff.SR) - round(c.start * ff.SR)
            f.write(data[:want] if len(data) >= want else np.pad(data, ((0, want - len(data)), (0, 0))))


def write_dub_track(paths: VideoPaths, doc: LinesDoc) -> Path:
    """The whole Japanese dub as one AAC track (the slow part of an export: ~1.3 s per minute of
    video). Written through a temporary name, so a half-written track never exists."""
    wav = paths.root / "dub_full.wav"
    tmp = paths.root / "dub.part.m4a"
    concat_dub(paths, doc, wav)
    try:
        ff.run_ffmpeg(["-i", str(wav), "-c:a", "aac", "-b:a", "192k", str(tmp)])
        tmp.replace(paths.dub_audio)
    finally:
        wav.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    return paths.dub_audio


def write_file(paths: VideoPaths, doc: LinesDoc, out: Path, title: str = "") -> Path:
    """Writes out (.mkv or .mp4) through a temporary name. Nothing is re-encoded, apart from the
    dub track the first time."""
    if not paths.dub_audio.exists():
        write_dub_track(paths, doc)
    mp4 = out.suffix.lower() == ".mp4"
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    try:
        ff.run_ffmpeg(
            [
                "-i",
                str(paths.video),
                "-i",
                str(paths.dub_audio),
                "-i",
                str(paths.audio),
                "-i",
                str(paths.subs / "ja.srt"),
                "-i",
                str(paths.subs / "en.srt"),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-map",
                "2:a:0",
                "-map",
                "3:0",
                "-map",
                "4:0",
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-c:s",
                "mov_text" if mp4 else "srt",
                *(["-movflags", "+faststart"] if mp4 else []),
                "-metadata",
                f"title={title}",
                "-metadata:s:a:0",
                "language=jpn",
                "-metadata:s:a:0",
                "title=日本語 (dub)",
                "-metadata:s:a:0",
                "handler_name=日本語 (dub)",
                "-metadata:s:a:1",
                "language=eng",
                "-metadata:s:a:1",
                "title=English (original)",
                "-metadata:s:a:1",
                "handler_name=English (original)",
                "-metadata:s:s:0",
                "language=jpn",
                "-metadata:s:s:0",
                "handler_name=日本語",
                "-metadata:s:s:1",
                "language=eng",
                "-metadata:s:s:1",
                "handler_name=English",
                "-disposition:a:0",
                "default",
                "-disposition:a:1",
                "0",
                "-disposition:s:0",
                "default",
                "-disposition:s:1",
                "0",
                str(tmp),
            ]
        )
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out
