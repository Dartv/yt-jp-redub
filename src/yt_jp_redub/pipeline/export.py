"""Offline output: one .mkv with ja/en audio and ja/en subtitles (milestone 2)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from ..models import LinesDoc, VideoPaths
from . import ff


def concat_dub(paths: VideoPaths, doc: LinesDoc, out: Path) -> None:
    with sf.SoundFile(str(out), "w", samplerate=ff.SR, channels=2, subtype="PCM_16") as f:
        for c in doc.chunks:
            data, sr = sf.read(str(paths.chunk_dir(c.id) / "dub.wav"), dtype="float32", always_2d=True)
            want = int(round(c.end * ff.SR)) - int(round(c.start * ff.SR))
            data = data[:want] if len(data) >= want else np.pad(data, ((0, want - len(data)), (0, 0)))
            f.write(data)


def write_mkv(paths: VideoPaths, doc: LinesDoc, out: Path, title: str = "") -> Path:
    dub_wav = paths.root / "dub_full.wav"
    concat_dub(paths, doc, dub_wav)
    ff.run_ffmpeg([
        "-i", str(paths.video), "-i", str(dub_wav), "-i", str(paths.audio),
        "-i", str(paths.subs / "ja.srt"), "-i", str(paths.subs / "en.srt"),
        "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0", "-map", "3:0", "-map", "4:0",
        "-c:v", "copy", "-c:a:0", "aac", "-b:a:0", "192k", "-c:a:1", "copy", "-c:s", "srt",
        "-metadata", f"title={title}",
        "-metadata:s:a:0", "language=jpn", "-metadata:s:a:0", "title=日本語 (dub)",
        "-metadata:s:a:1", "language=eng", "-metadata:s:a:1", "title=English (original)",
        "-metadata:s:s:0", "language=jpn", "-metadata:s:s:1", "language=eng",
        "-disposition:a:0", "default", "-disposition:a:1", "0",
        "-disposition:s:0", "default", "-disposition:s:1", "0",
        str(out),
    ])
    dub_wav.unlink(missing_ok=True)
    return out
