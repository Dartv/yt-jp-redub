"""Per-chunk dubbing: separate -> TTS per line (own vocals as reference) -> time-fit -> mix."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import soundfile as sf

from ..models import Chunk, Line, LinesDoc, VideoPaths
from . import ff

log = logging.getLogger(__name__)

GAP = 0.06            # minimum silence between consecutive dubbed lines
CTX = 1.0             # extra audio given to Demucs on both sides of a chunk


class PaceModel:
    """Learns seconds-per-character of the TTS voice at duration_factor 1.0."""

    def __init__(self, sec_per_char: float = 0.135):
        self.rate = sec_per_char

    def predict(self, text: str) -> float:
        return max(0.4, self.rate * _spoken_len(text))

    def update(self, text: str, natural_sec: float) -> None:
        n = _spoken_len(text)
        if n >= 4:
            self.rate = 0.8 * self.rate + 0.2 * (natural_sec / n)


def _spoken_len(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace() and ch not in "、。！？!?…「」『』（）()・ー～〜,.")  or 1


def _trim_silence(wav: np.ndarray, sr: int, thresh_db: float = -45.0) -> np.ndarray:
    mono = np.abs(wav).max(axis=1) if wav.ndim == 2 else np.abs(wav)
    loud = np.where(mono > 10 ** (thresh_db / 20))[0]
    if len(loud) == 0:
        return wav
    pad = int(0.02 * sr)
    return wav[max(0, loud[0] - pad): loud[-1] + pad]


def _reference(vocals: np.ndarray, sr: int, chunk: Chunk, line: Line, min_sec: float) -> np.ndarray:
    """The line's own vocals, padded to min_sec with neighbouring audio from the same chunk."""
    s, e = line.start - chunk.start, line.end - chunk.start + 0.1
    total = len(vocals) / sr
    short = min_sec - (e - s)
    if short > 0:
        s, e = s - short / 2, e + short / 2
        if s < 0:
            e, s = min(total, e - s), 0.0
        if e > total:
            s, e = max(0.0, s - (e - total)), total
    return vocals[int(max(0, s) * sr): int(min(total, e) * sr)]


def _time_stretch(wav: np.ndarray, sr: int, factor: float, tmp_dir: Path) -> np.ndarray:
    """Speeds audio up by `factor` (>1) with ffmpeg atempo; used only as a last resort."""
    src, dst = tmp_dir / "_stretch_in.wav", tmp_dir / "_stretch_out.wav"
    ff.write_wav(src, wav, sr)
    ff.run_ffmpeg(["-i", str(src), "-filter:a", f"atempo={min(2.0, factor):.4f}", str(dst)])
    out, _ = sf.read(str(dst), dtype="float32", always_2d=True)
    src.unlink(missing_ok=True)
    dst.unlink(missing_ok=True)
    return out


def dub_chunk(paths: VideoPaths, doc: LinesDoc, chunk: Chunk, separator, tts, cfg, pace: PaceModel) -> Path:
    """Writes chunks/<n>/dub.wav covering exactly [chunk.start, chunk.end] at 44.1 kHz stereo."""
    cdir = paths.chunk_dir(chunk.id)
    cdir.mkdir(parents=True, exist_ok=True)
    out_path = cdir / "dub.wav"
    lines = [doc.lines[i] for i in chunk.line_ids if doc.lines[i].ja]
    orig, sr = ff.read_slice(paths.audio_wav, chunk.start, chunk.end)
    n = len(orig)

    if not lines:  # no speech: keep the original audio
        ff.write_wav(out_path, orig, sr)
        return out_path

    # separation (with a little context on both sides for cleaner edges)
    vpath, bpath = cdir / "vocals.wav", cdir / "background.wav"
    if vpath.exists() and bpath.exists():
        vocals, _ = sf.read(str(vpath), dtype="float32", always_2d=True)
        background, _ = sf.read(str(bpath), dtype="float32", always_2d=True)
    else:
        pre = min(CTX, chunk.start)
        wide, _ = ff.read_slice(paths.audio_wav, chunk.start - pre, chunk.end + CTX)
        v, b = separator.separate(wide, sr)
        off = int(pre * sr)
        vocals, background = v[off:off + n], b[off:off + n]
        ff.write_wav(vpath, vocals, sr)
        ff.write_wav(bpath, background, sr)
    vocals = _pad_to(vocals, n)
    background = _pad_to(background, n)

    # references: own vocals; fall back to the nearest line with usable audio
    refs_dir, tts_dir = cdir / "refs", cdir / "tts"
    refs_dir.mkdir(exist_ok=True)
    tts_dir.mkdir(exist_ok=True)
    refs: dict[int, np.ndarray] = {}
    for l in lines:
        refs[l.id] = _reference(vocals, sr, chunk, l, cfg.tts.min_ref_sec)
    good = [l.id for l in lines if ff.rms_db(refs[l.id]) > -38 and len(refs[l.id]) > sr]
    ref_files: dict[int, Path] = {}
    for l in lines:
        rid = l.id
        if rid not in good and good:
            rid = min(good, key=lambda g: abs(doc.lines[g].start - l.start))
        p = refs_dir / f"{rid:05d}.wav"
        if not p.exists():
            ff.write_wav(p, refs[rid], sr)
        ref_files[l.id] = p

    voice = np.zeros((n, 1), dtype=np.float32)
    min_factor = 1.0 / cfg.tts.max_speedup
    cursor = 0.0
    for k, l in enumerate(lines):
        nxt_start = lines[k + 1].start - chunk.start if k + 1 < len(lines) else chunk.duration
        slot_start = l.start - chunk.start
        avail = max(0.3, nxt_start - slot_start - GAP)
        factor = float(np.clip(avail / pace.predict(l.ja), min_factor, 1.0))
        out = tts_dir / f"{l.id:05d}.wav"
        if not out.exists():
            tts.synth(l.ja, ref_files[l.id], out, factor)
        wav, tsr = sf.read(str(out), dtype="float32", always_2d=True)
        wav = _trim_silence(wav.mean(axis=1, keepdims=True), tsr)
        pace.update(l.ja, (len(wav) / tsr) / factor)
        wav = ff.resample(wav, tsr, sr)

        # loudness follows the original line (shouting stays loud), within ±10 dB of -20 dBFS
        orig_db = ff.rms_db(vocals[int(slot_start * sr): int((l.end - chunk.start) * sr)])
        target_db = float(np.clip(orig_db, -30, -10)) if orig_db > -50 else -20.0
        wav = wav * (10 ** ((target_db - ff.rms_db(wav)) / 20))

        start = max(slot_start, cursor + (GAP if cursor else 0.0))
        room = chunk.duration - start
        dur = len(wav) / sr
        if dur > room and room > 0.3:  # would run past the chunk: squeeze, then cut with a fade
            wav = _time_stretch(wav, sr, min(1.35, dur / room), cdir)
        a = int(start * sr)
        b = min(n, a + len(wav))
        seg = wav[: b - a].copy()
        if len(seg) < len(wav):
            fade = min(len(seg), int(0.08 * sr))
            seg[-fade:] *= np.linspace(1, 0, fade)[:, None]
        voice[a:b] += seg
        cursor = b / sr

    mix = background + np.repeat(voice, 2, axis=1)
    peak = float(np.abs(mix).max())
    if peak > 0.98:
        mix *= 0.98 / peak
    ff.write_wav(out_path, mix, sr)
    return out_path


def _pad_to(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    return np.pad(x, ((0, n - len(x)), (0, 0)))
