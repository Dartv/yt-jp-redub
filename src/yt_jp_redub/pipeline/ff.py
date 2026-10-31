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
    p = subprocess.run(
        cmd + args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(args[:12])}...\n{p.stderr[-2000:]}")


def probe(path: Path) -> dict:
    p = subprocess.run(
        [_bin("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {p.stderr}")
    return json.loads(p.stdout)


def stream(path: Path, kind: str) -> dict | None:
    """The first "video" or "audio" stream ffprobe finds in path."""
    return next((s for s in probe(path)["streams"] if s["codec_type"] == kind), None)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def to_wav(src: Path, dst: Path, sr: int = SR, channels: int = 2) -> None:
    """Written under a temporary name, so an interrupted conversion isn't mistaken for a finished one."""
    tmp = dst.with_suffix(".part.wav")
    run_ffmpeg(["-i", str(src), "-vn", "-ac", str(channels), "-ar", str(sr), "-c:a", "pcm_s16le", str(tmp)])
    tmp.replace(dst)


def read_slice(path: Path, start: float, end: float) -> tuple[np.ndarray, int]:
    """Returns float32 (frames, channels)."""
    info = sf.info(str(path))
    a, b = int(max(0, start) * info.samplerate), int(min(end, info.duration) * info.samplerate)
    data, sr = sf.read(str(path), start=a, stop=max(a, b), dtype="float32", always_2d=True)
    return data, sr


def decode_pcm(path: Path, sr: int = SR, channels: int = 2) -> np.ndarray:
    """Decodes any audio file to float32 (frames, channels)."""
    p = subprocess.run(
        [
            _bin("ffmpeg"),
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-ac",
            str(channels),
            "-ar",
            str(sr),
            "-",
        ],
        capture_output=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode {path}: {p.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(p.stdout, dtype=np.float32).reshape(-1, channels)


def fit_len(x: np.ndarray, n: int) -> np.ndarray:
    """x cut or padded with silence to n frames."""
    return x[:n] if len(x) >= n else np.pad(x, ((0, n - len(x)), (0, 0)))


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
