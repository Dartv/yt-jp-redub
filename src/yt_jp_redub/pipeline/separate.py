"""Demucs (htdemucs) source separation: vocals vs background (music + effects)."""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)


class DemucsSeparator:
    def __init__(self, model: str = "htdemucs", device: str = "cuda"):
        import torch
        from demucs.pretrained import get_model

        if device == "cuda" and not torch.cuda.is_available():
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = device
        self.model = get_model(model)
        self.model.to(device).eval()
        self.sr = self.model.samplerate
        self.vocals_idx = self.model.sources.index("vocals")
        self._lock = threading.Lock()
        log.info("Demucs %s on %s", model, device)

    def separate(self, audio: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
        """audio: float32 (frames, channels) -> (vocals, background), same shape and rate."""
        import torch
        from demucs.apply import apply_model

        from .ff import resample

        x = audio if audio.shape[1] == 2 else np.repeat(audio[:, :1], 2, axis=1)
        x = resample(x, sr, self.sr)
        wav = torch.from_numpy(np.ascontiguousarray(x.T))
        ref = wav.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        with self._lock, torch.no_grad():
            out = apply_model(
                self.model,
                ((wav - mean) / std)[None].to(self.device),
                shifts=0,
                split=True,
                overlap=0.25,
                progress=False,
            )[0]
            out = (out * std + mean).cpu().numpy()  # (sources, channels, frames)
            if self.device.startswith("cuda"):  # its ~0.8 GB of scratch memory goes to IndexTTS until the next chunk
                torch.cuda.empty_cache()
        vocals = out[self.vocals_idx].T
        background = out.sum(0).T - vocals
        vocals, background = resample(vocals, self.sr, sr), resample(background, self.sr, sr)
        n = len(audio)
        return _fit(vocals, n), _fit(background, n)


class MockSeparator:
    def separate(self, audio: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
        return audio.copy(), audio * 0.15


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n].astype(np.float32)
    return np.pad(x, ((0, n - len(x)), (0, 0))).astype(np.float32)


def make_separator(cfg):
    if cfg.separate.backend == "mock":
        return MockSeparator()
    return DemucsSeparator(cfg.separate.model, cfg.separate.device)
