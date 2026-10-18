"""English ASR with word timestamps: Parakeet TDT 0.6B v2 through onnx-asr.

Silero VAD splits the audio into speech segments (well under the model's
length limit); token timestamps are merged into words.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..config import ROOT
from ..models import Word

log = logging.getLogger(__name__)
_MODEL_CACHE: dict = {}
MODELS_DIR = ROOT / "models"


def _providers(device: str) -> list[str]:
    import onnxruntime as ort

    avail = ort.get_available_providers()
    if device == "cuda" and "CUDAExecutionProvider" in avail:
        _preload_cuda_dlls()
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if device == "cuda" and "CoreMLExecutionProvider" in avail:
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def _preload_cuda_dlls() -> None:
    """onnxruntime-gpu can reuse the CUDA/cuDNN libraries that ship with torch."""
    try:
        import onnxruntime as ort
        import torch  # noqa: F401  (loads its bundled CUDA libraries)

        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls(cuda=True, cudnn=True, msvc=True, directory="")
    except Exception as e:  # pragma: no cover
        log.debug("CUDA DLL preload skipped: %s", e)


def load(model_name: str, device: str):
    key = (model_name, device)
    if key not in _MODEL_CACHE:
        import onnx_asr

        prov = _providers(device)
        log.info("Loading ASR %s on %s", model_name, prov[0])
        # A plain local directory: onnxruntime rejects external weight data behind HF cache symlinks.
        model = onnx_asr.load_model(model_name, path=MODELS_DIR / model_name, providers=prov)
        vad = onnx_asr.load_vad("silero", path=MODELS_DIR / "silero-vad", providers=["CPUExecutionProvider"])
        _MODEL_CACHE[key] = model.with_vad(
            vad, max_speech_duration_s=25, min_silence_duration_ms=250, speech_pad_ms=60, batch_size=4
        ).with_timestamps()
    return _MODEL_CACHE[key]


def tokens_to_words(tokens: list[str], stamps: list[float], seg_start: float, seg_end: float) -> list[Word]:
    """Merges subword tokens into words. A token starting with a space (or ▁) starts a new word."""
    words: list[Word] = []
    cur, cur_start = "", None
    for tok, ts in zip(tokens, stamps, strict=False):
        starts_word = tok.startswith((" ", "▁")) or not cur
        piece = tok.replace("▁", " ")
        if starts_word and cur.strip():
            words.append(Word(cur.strip(), cur_start, seg_start + ts))
            cur, cur_start = "", None
        if cur_start is None:
            cur_start = seg_start + ts
        cur += piece
    if cur.strip():
        words.append(Word(cur.strip(), cur_start, seg_end))
    # A word ends where the next one starts (minus a little), but no later than 1.2 s after its start
    for i, w in enumerate(words):
        nxt = words[i + 1].start if i + 1 < len(words) else seg_end
        w.end = round(min(nxt, w.start + max(0.08, 0.09 * len(w.text) + 0.15), seg_end), 3)
        w.start = round(w.start, 3)
    return words


def transcribe(
    wav16k: np.ndarray, model_name: str, device: str, progress: Callable[[float], None] | None = None
) -> list[Word]:
    """wav16k: float32 mono at 16 kHz. Returns words with absolute timestamps."""
    asr = load(model_name, device)
    total = len(wav16k) / 16000
    words: list[Word] = []
    for seg in asr.recognize(wav16k, sample_rate=16000):
        if seg.tokens and seg.timestamps:
            words.extend(tokens_to_words(seg.tokens, seg.timestamps, seg.start, seg.end))
        elif seg.text.strip():
            words.append(Word(seg.text.strip(), seg.start, seg.end))
        if progress and total:
            progress(min(1.0, seg.end / total))
    words.sort(key=lambda w: w.start)
    return words


def load_audio_16k(path: Path) -> np.ndarray:
    import soundfile as sf

    from .ff import resample

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return resample(data.mean(axis=1, keepdims=True), sr, 16000)[:, 0]
