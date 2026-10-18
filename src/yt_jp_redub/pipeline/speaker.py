"""Speaker embeddings (WeSpeaker CAM++, Apache-2.0) to tell whether adjacent lines share a speaker."""

from __future__ import annotations

import logging
import threading

import numpy as np

from ..config import ROOT

log = logging.getLogger(__name__)
REPO, FILE = "Wespeaker/wespeaker-voxceleb-campplus-LM", "voxceleb_CAM++_LM.onnx"
MODEL_DIR = ROOT / "models" / "wespeaker-campplus"
_lock = threading.Lock()
_model = None


def _load():
    global _model
    with _lock:
        if _model is None:
            from huggingface_hub import hf_hub_download
            from onnx_asr.loader import Manager
            if not (MODEL_DIR / FILE).exists():
                hf_hub_download(REPO, FILE, local_dir=MODEL_DIR)
            _model = Manager(providers=["CPUExecutionProvider"]).create_se(local_dir=MODEL_DIR, offline=True)
    return _model


def embed(wav16k: np.ndarray) -> np.ndarray | None:
    """Unit-length embedding of a mono 16 kHz clip; None when the clip is too short to judge."""
    if len(wav16k) < 16000 * 0.5:
        return None
    x = _load().embedding(np.ascontiguousarray(wav16k, dtype=np.float32))
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    return x / (np.linalg.norm(x) + 1e-9)


def available() -> bool:
    try:
        _load()
        return True
    except Exception as e:  # offline without the model: grouping is just skipped
        log.warning("speaker model unavailable, lines are voiced one by one: %s", e)
        return False
