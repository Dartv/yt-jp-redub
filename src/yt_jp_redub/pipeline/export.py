"""Offline output: one .mkv with ja/en audio and ja/en subtitles, built from the HLS segments."""

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


def write_mkv(paths: VideoPaths, doc: LinesDoc, out: Path, title: str = "") -> Path:
    dub_wav = paths.root / "dub_full.wav"
    concat_dub(paths, doc, dub_wav)
    try:
        ff.run_ffmpeg(
            [
                "-i",
                str(paths.video),
                "-i",
                str(dub_wav),
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
                "-c:a:0",
                "aac",
                "-b:a:0",
                "192k",
                "-c:a:1",
                "copy",
                "-c:s",
                "srt",
                "-metadata",
                f"title={title}",
                "-metadata:s:a:0",
                "language=jpn",
                "-metadata:s:a:0",
                "title=日本語 (dub)",
                "-metadata:s:a:1",
                "language=eng",
                "-metadata:s:a:1",
                "title=English (original)",
                "-metadata:s:s:0",
                "language=jpn",
                "-metadata:s:s:1",
                "language=eng",
                "-disposition:a:0",
                "default",
                "-disposition:a:1",
                "0",
                "-disposition:s:0",
                "default",
                "-disposition:s:1",
                "0",
                str(out),
            ]
        )
    finally:
        dub_wav.unlink(missing_ok=True)
    return out
