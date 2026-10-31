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
from .segment import continues
from .speakable import speakable

log = logging.getLogger(__name__)

GAP = 0.06  # minimum silence between consecutive utterances of one speaker
CROSSTALK = 0.4  # how far a take may start over the end of another speaker's
MAX_CATCH_UP = 1.15  # extra speed-up (atempo) for a take that would run into the next line
SENTENCE_GAP = 1.0  # the lines of one sentence this close together are voiced as one take
CTX = 1.0  # extra audio given to Demucs on both sides of a chunk


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
        """What the voice says (digits and symbols spelled out)."""
        return speakable("".join(l.ja.strip() for l in self.lines))


Embeddings = dict[int, np.ndarray]  # line id -> unit speaker embedding (near-silent lines have none)
MERGE_MIN_SEC = 0.8  # a line must be this long to tell its speaker reliably enough to merge it


def line_embeddings(lines: list[Line], vocals16k: np.ndarray, offset: float) -> Embeddings:
    """Who speaks each line, from that line's own audio only: in fast dialogue anything around it
    is the other speaker. A short line's audio is repeated to the length the model needs, which
    is good enough to find its speaker's other lines. Empty when the speaker model is unavailable."""
    from . import speaker

    if not speaker.available():
        return {}
    out = {}
    for l in lines:
        own = vocals16k[int(max(0.0, l.start - offset) * 16000) : int(max(0.0, l.end - offset) * 16000)]
        if len(own) < 0.25 * 16000:
            continue
        e = speaker.embed(np.tile(own, -(-16000 // len(own))))
        if e is not None:
            out[l.id] = e
    return out


def _same_speaker(embs: Embeddings, a: int, b: int, sim: float) -> bool:
    return a in embs and b in embs and float(embs[a] @ embs[b]) >= sim


def group_utterances(lines: list[Line], embs: Embeddings, cfg) -> list[Utterance]:
    """Merges adjacent lines into one take: the lines of one sentence (Japanese word order moves words
    between them, so only the whole sentence fits its time), and lines of one speaker when the gap
    is short and the voices clearly match."""
    utts = [Utterance([l]) for l in lines]
    if not cfg.group_speakers or not utts:
        return utts
    out = [utts[0]]
    for u in utts[1:]:
        prev, a, b = out[-1], out[-1].lines[-1], u.lines[0]
        gap = u.start - prev.end
        # ASR often drops the full stop after an exclamation: a "continuation" in another voice isn't one
        other = a.id in embs and b.id in embs and not _same_speaker(embs, a.id, b.id, cfg.speaker_sim)
        sentence = continues(a) and gap <= SENTENCE_GAP and not other
        same = (
            gap <= cfg.group_gap_sec
            and min(a.end - a.start, b.end - b.start) >= MERGE_MIN_SEC
            and _same_speaker(embs, a.id, b.id, cfg.speaker_sim)
        )
        if (sentence or same) and u.end - prev.start <= cfg.group_max_sec:
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


ENV_HOP = 0.02


def _envelope_db(x: np.ndarray, sr: int, hop: float = ENV_HOP) -> np.ndarray:
    h = int(hop * sr)
    n = len(x) // h
    e = np.sqrt(np.square(x[: n * h].reshape(n, h)).mean(axis=1)) + 1e-9
    return 20 * np.log10(e)


def _trim_silence(wav: np.ndarray, sr: int, below_peak_db: float = 35.0) -> np.ndarray:
    """Drops what comes before and after the speech: silence, and the low breath and noise TTS
    often puts first (a median 0.2 s on IndexTTS-2.5, which adds up in fast dialogue). Speech is
    where the 10 ms loudness is within `below_peak_db` of the utterance's peak; a little attack and
    decay around it are kept."""
    hop = int(0.01 * sr)
    env = _envelope_db(wav.mean(axis=1) if wav.ndim == 2 else wav, sr, 0.01)
    if not len(env):
        return wav
    loud = np.where(env > max(-50.0, float(env.max()) - below_peak_db))[0]
    if not len(loud):  # all quiet: nothing to trim against
        return wav
    return wav[max(0, loud[0] * hop - int(0.03 * sr)) : (loud[-1] + 1) * hop + int(0.08 * sr)]


def _fade(wav: np.ndarray, sr: int, fade_in: float = 0.012, fade_out: float = 0.05) -> np.ndarray:
    wav = wav.copy()
    a, b = min(len(wav), int(fade_in * sr)), min(len(wav), int(fade_out * sr))
    if a:
        wav[:a] *= np.linspace(0, 1, a)[:, None]
    if b:
        wav[-b:] *= np.linspace(1, 0, b)[:, None]
    return wav


def _reference(
    vocals: np.ndarray, sr: int, start: float, end: float, min_sec: float, max_sec: float = 12.0
) -> np.ndarray:
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
    return vocals[int(max(0, s) * sr) : int(min(total, e) * sr)]


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
        vocals, background = v[off : off + n], b[off : off + n]
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
    if not lines:  # no speech: keep the original audio
        ff.write_wav(out_path, orig, sr)
        return out_path

    n = len(orig)
    vocals, background = _separate(paths, chunk, separator, sr, n)
    vocals16k = ff.resample(vocals.mean(axis=1, keepdims=True), sr, 16000)[:, 0]
    embs = line_embeddings(lines, vocals16k, chunk.start)
    utts = group_utterances(lines, embs, cfg.tts)
    refs = _write_references(utts, vocals, sr, chunk, cdir / "refs", embs, cfg.tts)
    (cdir / "tts").mkdir(exist_ok=True)

    env_db = _envelope_db(vocals.mean(axis=1), sr)
    takes = []
    for k, u in enumerate(utts):
        rel = u.start - chunk.start
        next_start = utts[k + 1].start - chunk.start if k + 1 < len(utts) else chunk.duration
        speech_end = _speech_end(env_db, ENV_HOP, rel, u.end - chunk.start, next_start - GAP)
        target = max(0.3, min(speech_end, next_start - GAP) - rel)  # the time the original took
        wav, tsr = _fit(
            tts, u, refs[u.id], cdir / "tts" / f"{u.id:05d}-{len(u.lines)}.wav", target, pace, cfg.tts
        )
        takes.append(
            _match_loudness(
                ff.resample(wav, tsr, sr), vocals[int(rel * sr) : int((u.end - chunk.start) * sr)]
            )
        )
    voice = _lay_out(utts, takes, sr, n, chunk, embs, cfg.tts, cdir)

    mix = background + np.repeat(voice, 2, axis=1)
    peak = float(np.abs(mix).max())
    if peak > 0.98:
        mix *= 0.98 / peak
    ff.write_wav(out_path, mix, sr)
    log.debug("chunk %d: %d lines voiced as %d utterances", chunk.id, len(lines), len(utts))
    return out_path


def _lay_out(
    utts: list[Utterance],
    takes: list[np.ndarray],
    sr: int,
    n: int,
    chunk: Chunk,
    embs: Embeddings,
    cfg,
    tmp_dir,
) -> np.ndarray:
    """The chunk's voice track: each take at its line's original onset. In fast dialogue the takes
    need more time than the lines took, so a delay would build up line after line: instead a take
    may start over the end of the previous speaker's (people talk over each other in an argument),
    and a take that would run into the next line is sped up to make the time."""

    def other_speaker(k: int) -> bool:  # takes k-1 and k are clearly different voices
        a, b = utts[k - 1].lines[-1].id, utts[k].lines[0].id
        return a in embs and b in embs and not _same_speaker(embs, a, b, cfg.speaker_sim)

    voice = np.zeros((n, 1), dtype=np.float32)
    cursor = 0.0
    for k, (u, wav) in enumerate(zip(utts, takes, strict=True)):
        rel = u.start - chunk.start
        start = rel if k == 0 else max(rel, cursor - (CROSSTALK if other_speaker(k) else -GAP))
        if k + 1 < len(utts):
            room = utts[k + 1].start - chunk.start - start + (CROSSTALK if other_speaker(k + 1) else -GAP)
            if len(wav) / sr > room * 1.03:
                wav = _time_stretch(wav, sr, min(MAX_CATCH_UP, len(wav) / sr / max(room, 0.1)), tmp_dir)
        cursor = _place(voice, wav, sr, start, rel, chunk.duration, tmp_dir, u.id)
    return voice


def _write_references(
    utts: list[Utterance],
    vocals: np.ndarray,
    sr: int,
    chunk: Chunk,
    refs_dir: Path,
    embs: Embeddings,
    cfg,
) -> dict[int, Path]:
    """Each utterance's own original vocals as its voice reference. A short one is topped up to
    min_ref_sec with the same speaker's nearest other speech, a quiet one uses that speech alone:
    never with the audio right next to it, which in fast dialogue is the other speaker. Without
    speaker embeddings, the neighbouring audio is all there is."""
    refs_dir.mkdir(exist_ok=True)
    own = {u.id: _reference(vocals, sr, u.start - chunk.start, u.end - chunk.start, 0.0) for u in utts}
    loud = {u.id for u in utts if ff.rms_db(own[u.id]) > -38}
    gap = np.zeros((int(0.15 * sr), vocals.shape[1]), dtype=vocals.dtype)
    out = {}
    for u in utts:
        p = out[u.id] = refs_dir / f"{u.id:05d}.wav"
        if p.exists():
            continue
        speaker_id = u.lines[0].id
        parts = [own[u.id]] if u.id in loud else []
        if u.id in loud and len(own[u.id]) >= cfg.min_ref_sec * sr:
            clip = own[u.id]
        elif speaker_id in embs:
            same = sorted(
                (
                    g
                    for g in utts
                    if g is not u
                    and g.id in loud
                    and _same_speaker(embs, speaker_id, g.lines[0].id, cfg.speaker_sim)
                ),
                key=lambda g: abs(g.start - u.start),
            )
            for g in same:
                if sum(len(x) for x in parts) >= cfg.min_ref_sec * sr:
                    break
                parts += [gap, own[g.id]] if parts else [own[g.id]]
            clip = np.concatenate(parts) if parts else own[u.id]
        else:
            clip = _reference(vocals, sr, u.start - chunk.start, u.end - chunk.start, cfg.min_ref_sec)
        ff.write_wav(p, clip[: int(12 * sr)], sr)
    return out


def _fit(
    tts, u: Utterance, ref: Path, out: Path, target: float, pace: PaceModel, cfg
) -> tuple[np.ndarray, int]:
    """Synthesizes the utterance paced to last about `target` seconds (within the speed limits).
    A badly missed prediction gets one corrective take: too short sounds like stopping early,
    too long pushes later lines back."""
    lo, hi = 1.0 / cfg.max_speedup, cfg.max_slowdown
    factor = float(np.clip(target / pace.predict(u.text), lo, hi))
    wav, tsr, factor = _synth(tts, u, ref, out, factor)
    natural = (len(wav) / tsr) / factor
    pace.update(u.text, natural)
    better = float(np.clip(target / natural, lo, hi))
    if abs(len(wav) / tsr - target) > max(0.4, 0.2 * target) and abs(better - factor) > 0.05:
        wav, tsr, factor = _synth(tts, u, ref, out, better, force=True)
    log.debug(
        "utt %d lines=%d target=%.2f factor=%.2f got=%.2f", u.id, len(u.lines), target, factor, len(wav) / tsr
    )
    return wav, tsr


def _match_loudness(wav: np.ndarray, original: np.ndarray) -> np.ndarray:
    """Follows the original speech (shouting stays loud), within ±10 dB of -20 dBFS."""
    orig_db = ff.rms_db(original)
    target_db = float(np.clip(orig_db, -30, -10)) if orig_db > -50 else -20.0
    return wav * (10 ** ((target_db - ff.rms_db(wav)) / 20))


def _place(
    voice: np.ndarray,
    wav: np.ndarray,
    sr: int,
    start: float,
    rel: float,
    chunk_len: float,
    tmp_dir: Path,
    uid: int,
) -> float:
    """Mixes `wav` into the voice track at `start`; returns where it ends. Speech that would run
    past the chunk is squeezed, then started earlier, and cut with a fade only as a last resort."""
    room = chunk_len - start
    if len(wav) / sr > room:
        wav = _time_stretch(wav, sr, min(1.35, (len(wav) / sr) / max(room, 0.1)), tmp_dir)
        if len(wav) / sr > room:
            new_start = min(max(rel - 0.5, chunk_len - len(wav) / sr, 0.0), max(0.0, chunk_len - 0.3))
            log.warning(
                "utterance %d does not fit its chunk; overlapping by %.2fs", uid, max(0.0, start - new_start)
            )
            start = new_start
    wav = _fade(wav, sr)
    n = len(voice)
    a = min(int(start * sr), max(0, n - 1))
    b = min(n, a + len(wav))
    seg = wav[: b - a]
    if len(seg) < len(wav):
        seg = _fade(seg, sr, fade_in=0, fade_out=0.08)
    voice[a:b] += seg
    return b / sr


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
