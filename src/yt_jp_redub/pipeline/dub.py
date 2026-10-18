"""Per-chunk dubbing: separate -> group lines into utterances -> TTS (own vocals as reference)
-> time-fit -> mix.

Consecutive lines of the same speaker are voiced as one utterance, so a speaker's
sentences flow into each other instead of being stitched from fragments; each
utterance is paced to fill the time the original speech took.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from ..models import Chunk, Line, LinesDoc, VideoPaths
from . import ff
from .pace import PaceModel

log = logging.getLogger(__name__)

GAP = 0.06            # minimum silence between consecutive utterances
CTX = 1.0             # extra audio given to Demucs on both sides of a chunk


@dataclass
class Utterance:
    lines: list[Line]

    @property
    def id(self) -> int:
        return self.lines[0].id

    @property
    def start(self) -> float:
        return self.lines[0].start

    @property
    def end(self) -> float:
        return self.lines[-1].end

    @property
    def text(self) -> str:
        return "".join(l.ja.strip() for l in self.lines)


def group_utterances(lines: list[Line], vocals16k: np.ndarray, offset: float, cfg) -> list[Utterance]:
    """Merges adjacent lines when the gap is short and the voices clearly match."""
    utts = [Utterance([l]) for l in lines]
    if not cfg.group_speakers or len(lines) < 2:
        return utts
    from . import speaker
    if not speaker.available():
        return utts

    def emb(l: Line):
        a, b = int((l.start - offset) * 16000), int((l.end - offset) * 16000)
        return speaker.embed(vocals16k[max(0, a): max(0, b)])

    embs = {l.id: emb(l) for l in lines}
    out = [utts[0]]
    for u in utts[1:]:
        prev = out[-1]
        a, b = embs[prev.lines[-1].id], embs[u.id]
        same = a is not None and b is not None and float(a @ b) >= cfg.speaker_sim
        if same and u.start - prev.end <= cfg.group_gap_sec and u.end - prev.start <= cfg.group_max_sec:
            prev.lines.extend(u.lines)
        else:
            out.append(u)
    return out


def _speech_end(env_db: np.ndarray, hop: float, start: float, end: float, limit: float) -> float:
    """Where the original speaker actually stops. ASR word ends are estimates (TDT gives token
    starts), so follow the clean vocals stem past `end` while it stays within 30 dB of the line's
    peak, up to `limit` (the next line). Same idea as VAD-"stable timestamps", on the Demucs stem."""
    a, b, lim = int(start / hop), int(end / hop), int(limit / hop)
    if b <= a or a >= len(env_db):
        return end
    floor = max(-45.0, float(env_db[a:b].max()) - 30.0)
    i, quiet = b, 0
    while i < min(lim, len(env_db)):
        quiet = quiet + 1 if env_db[i] < floor else 0
        if quiet * hop >= 0.15:  # 150 ms of quiet ends the speech
            return max(end, (i - quiet + 1) * hop)
        i += 1
    return max(end, min(limit, i * hop))


def _envelope_db(x: np.ndarray, sr: int, hop: float = 0.02) -> np.ndarray:
    h = int(hop * sr)
    n = len(x) // h
    e = np.sqrt(np.square(x[: n * h].reshape(n, h)).mean(axis=1)) + 1e-9
    return 20 * np.log10(e)


def _trim_silence(wav: np.ndarray, sr: int, thresh_db: float = -50.0) -> np.ndarray:
    """Drops leading/trailing near-silence but keeps breath and decay around the speech."""
    mono = np.abs(wav).max(axis=1) if wav.ndim == 2 else np.abs(wav)
    loud = np.where(mono > 10 ** (thresh_db / 20))[0]
    if len(loud) == 0:
        return wav
    return wav[max(0, loud[0] - int(0.03 * sr)): loud[-1] + int(0.08 * sr)]


def _fade(wav: np.ndarray, sr: int, fade_in: float = 0.012, fade_out: float = 0.05) -> np.ndarray:
    wav = wav.copy()
    a, b = min(len(wav), int(fade_in * sr)), min(len(wav), int(fade_out * sr))
    if a:
        wav[:a] *= np.linspace(0, 1, a)[:, None]
    if b:
        wav[-b:] *= np.linspace(1, 0, b)[:, None]
    return wav


def _reference(vocals: np.ndarray, sr: int, start: float, end: float, min_sec: float, max_sec: float = 12.0) -> np.ndarray:
    """The original vocals of [start, end] (chunk-relative), padded to min_sec with neighbouring audio."""
    s, e = start, end + 0.1
    total = len(vocals) / sr
    short = min_sec - (e - s)
    if short > 0:
        s, e = s - short / 2, e + short / 2
        if s < 0:
            e, s = min(total, e - s), 0.0
        if e > total:
            s, e = max(0.0, s - (e - total)), total
    e = min(e, s + max_sec)
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


def _separate(paths: VideoPaths, chunk: Chunk, separator, sr: int, n: int) -> tuple[np.ndarray, np.ndarray]:
    cdir = paths.chunk_dir(chunk.id)
    vpath, bpath = cdir / "vocals.wav", cdir / "background.wav"
    if vpath.exists() and bpath.exists():
        vocals, _ = sf.read(str(vpath), dtype="float32", always_2d=True)
        background, _ = sf.read(str(bpath), dtype="float32", always_2d=True)
    else:
        pre = min(CTX, chunk.start)  # a little context on both sides gives cleaner edges
        wide, _ = ff.read_slice(paths.audio_wav, chunk.start - pre, chunk.end + CTX)
        v, b = separator.separate(wide, sr)
        off = int(pre * sr)
        vocals, background = v[off:off + n], b[off:off + n]
        ff.write_wav(vpath, vocals, sr)
        ff.write_wav(bpath, background, sr)
    return _pad_to(vocals, n), _pad_to(background, n)


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

    vocals, background = _separate(paths, chunk, separator, sr, n)
    vocals16k = ff.resample(vocals.mean(axis=1, keepdims=True), sr, 16000)[:, 0]
    utts = group_utterances(lines, vocals16k, chunk.start, cfg.tts)

    # references: the utterance's own vocals; fall back to the nearest one with usable audio
    refs_dir, tts_dir = cdir / "refs", cdir / "tts"
    refs_dir.mkdir(exist_ok=True)
    tts_dir.mkdir(exist_ok=True)
    refs = {u.id: _reference(vocals, sr, u.start - chunk.start, u.end - chunk.start, cfg.tts.min_ref_sec)
            for u in utts}
    good = [u for u in utts if ff.rms_db(refs[u.id]) > -38 and len(refs[u.id]) > sr]
    ref_files: dict[int, Path] = {}
    for u in utts:
        src = u
        if u not in good and good:
            src = min(good, key=lambda g: abs(g.start - u.start))
        p = refs_dir / f"{src.id:05d}.wav"
        if not p.exists():
            ff.write_wav(p, refs[src.id], sr)
        ref_files[u.id] = p

    voice = np.zeros((n, 1), dtype=np.float32)
    min_factor, max_factor = 1.0 / cfg.tts.max_speedup, cfg.tts.max_slowdown
    env_db, hop = _envelope_db(vocals.mean(axis=1), sr), 0.02
    cursor = 0.0
    for k, u in enumerate(utts):
        nxt_start = utts[k + 1].start - chunk.start if k + 1 < len(utts) else chunk.duration
        slot_start = u.start - chunk.start
        speech_end = _speech_end(env_db, hop, slot_start, u.end - chunk.start, nxt_start - GAP)
        # pace from where this utterance will really start: after a spill, later ones speed up to catch up
        start = max(slot_start, cursor + (GAP if cursor else 0.0))
        # fill the time the original speech took, never past the next utterance
        target = max(0.3, min(speech_end, nxt_start - GAP) - start)
        factor = float(np.clip(target / pace.predict(u.text), min_factor, max_factor))
        out = tts_dir / f"{u.id:05d}-{len(u.lines)}.wav"
        wav, tsr, factor = _synth(tts, u, ref_files[u.id], out, factor)
        natural = (len(wav) / tsr) / factor
        pace.update(u.text, natural)
        # one corrective take when the prediction missed badly (too short sounds like stopping early,
        # too long means speeding up later lines)
        better = float(np.clip(target / natural, min_factor, max_factor))
        miss = len(wav) / tsr - target
        if abs(miss) > max(0.4, 0.2 * target) and abs(better - factor) > 0.05:
            wav, tsr, factor = _synth(tts, u, ref_files[u.id], out, better, force=True)
        log.debug("utt %d lines=%d target=%.2f factor=%.2f got=%.2f late=%.2f early=%.2f", u.id, len(u.lines), target,
                  factor, len(wav) / tsr, start - slot_start, speech_end - (start + len(wav) / tsr))
        wav = ff.resample(wav, tsr, sr)

        # loudness follows the original speech (shouting stays loud), within ±10 dB of -20 dBFS
        orig_db = ff.rms_db(vocals[int(slot_start * sr): int((u.end - chunk.start) * sr)])
        target_db = float(np.clip(orig_db, -30, -10)) if orig_db > -50 else -20.0
        wav = wav * (10 ** ((target_db - ff.rms_db(wav)) / 20))

        room = chunk.duration - start
        if len(wav) / sr > room:  # would run past the chunk: squeeze, then cut with a fade
            wav = _time_stretch(wav, sr, min(1.35, (len(wav) / sr) / max(room, 0.1)), cdir)
            if len(wav) / sr > room:
                # last resort: start earlier (overlapping the previous tail) rather than drop speech
                start = max(slot_start - 0.5, chunk.duration - len(wav) / sr, 0.0)
                start = min(start, max(0.0, chunk.duration - 0.3))
                log.warning("utterance %d does not fit its chunk; overlapping by %.2fs", u.id, max(0, cursor - start))
        wav = _fade(wav, sr)
        a = min(int(start * sr), max(0, n - 1))
        b = min(n, a + len(wav))
        seg = wav[: b - a]
        if len(seg) < len(wav):
            seg = _fade(seg, sr, fade_in=0, fade_out=0.08)
        voice[a:b] += seg
        cursor = b / sr

    mix = background + np.repeat(voice, 2, axis=1)
    peak = float(np.abs(mix).max())
    if peak > 0.98:
        mix *= 0.98 / peak
    ff.write_wav(out_path, mix, sr)
    log.debug("chunk %d: %d lines voiced as %d utterances", chunk.id, len(lines), len(utts))
    return out_path


def _synth(tts, u: Utterance, ref: Path, out: Path, factor: float, force: bool = False):
    """Synthesizes (or reuses) an utterance; returns trimmed mono audio, its rate and the factor used."""
    meta = out.with_suffix(".factor")
    if force or not out.exists():
        tts.synth(u.text, ref, out, factor)
        meta.write_text(f"{factor:.4f}")
    elif meta.exists():
        factor = float(meta.read_text())
    wav, tsr = sf.read(str(out), dtype="float32", always_2d=True)
    return _trim_silence(wav.mean(axis=1, keepdims=True), tsr), tsr, factor


def _pad_to(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    return np.pad(x, ((0, n - len(x)), (0, 0)))
