"""ffmpeg/ffprobe helpers and small audio I/O utilities."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100  # working sample rate for separation, mixing and output


def _bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} not found on PATH. Install ffmpeg (see README).")
    return path


def run_ffmpeg(args: list[str], quiet: bool = True, cwd: Path | None = None) -> None:
    cmd = [_bin("ffmpeg"), "-y", "-hide_banner", "-nostdin"]
    if quiet:
        cmd += ["-loglevel", "error"]
    p = subprocess.run(cmd + args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(args[:12])}...\n{p.stderr[-2000:]}")


def probe(path: Path) -> dict:
    p = subprocess.run([_bin("ffprobe"), "-v", "error", "-print_format", "json", "-show_format",
                        "-show_streams", str(path)], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {p.stderr}")
    return json.loads(p.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def to_wav(src: Path, dst: Path, sr: int = SR, channels: int = 2) -> None:
    run_ffmpeg(["-i", str(src), "-vn", "-ac", str(channels), "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)])


def read_slice(path: Path, start: float, end: float) -> tuple[np.ndarray, int]:
    """Returns float32 (frames, channels)."""
    info = sf.info(str(path))
    a, b = int(max(0, start) * info.samplerate), int(min(end, info.duration) * info.samplerate)
    data, sr = sf.read(str(path), start=a, stop=max(a, b), dtype="float32", always_2d=True)
    return data, sr


def write_wav(path: Path, data: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.wav")
    sf.write(str(tmp), data, sr, subtype="PCM_16")
    tmp.replace(path)


def resample(data: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to or len(data) == 0:
        return data
    import torch
    import torchaudio.functional as F
    t = torch.from_numpy(np.ascontiguousarray(data.T))
    return F.resample(t, sr_from, sr_to).numpy().T.astype(np.float32)


def rms_db(x: np.ndarray) -> float:
    if x.size == 0:
        return -120.0
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9))
