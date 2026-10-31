"""TTS backends. IndexTTS-2.5 runs as a subprocess in its own environment."""

from __future__ import annotations

import itertools
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from .pace import max_speech_sec

log = logging.getLogger(__name__)
WORKER = Path(__file__).resolve().parents[1] / "tts_worker" / "indextts_server.py"
MEL_TOKENS_PER_SEC = 50  # IndexTTS-2.5's speech tokens: 1.72 mel frames each at 22050 Hz / 256


def _venv_python(project: Path) -> Path:
    win = project / ".venv" / "Scripts" / "python.exe"
    return win if win.exists() else project / ".venv" / "bin" / "python"


class IndexTTSClient:
    def __init__(
        self,
        indextts_dir: Path,
        lang: str = "JA",
        use_bf16: bool = True,
        emo_alpha: float = 1.0,
        num_beams: int = 1,
    ):
        self.dir, self.lang, self.emo_alpha, self.num_beams = indextts_dir, lang, emo_alpha, num_beams
        py = _venv_python(indextts_dir)
        if not py.exists():
            raise RuntimeError(f"IndexTTS environment not found at {indextts_dir} (run setup).")
        if not (indextts_dir / "checkpoints" / "gpt.pth").exists():
            raise RuntimeError(f"IndexTTS-2.5 weights missing in {indextts_dir / 'checkpoints'} (run setup).")
        cmd = [str(py), str(WORKER), "--model-dir", "checkpoints"] + (["--bf16"] if use_bf16 else [])
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        env.pop("VIRTUAL_ENV", None)
        # the worker's own output; kept open for the process's lifetime, closed in close()
        self._stderr_log = open(  # noqa: SIM115
            indextts_dir.parent / "indextts_worker.log", "a", encoding="utf-8", errors="replace"
        )
        log.info("Starting IndexTTS worker")
        self.proc = subprocess.Popen(
            cmd,
            cwd=indextts_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_log,
            text=True,
            encoding="utf-8",
            env=env,
            bufsize=1,
        )
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        ready = self._read()
        if not ready.get("ready"):
            raise RuntimeError(f"IndexTTS worker failed to start: {ready}")
        log.info("IndexTTS ready on %s", ready.get("device"))

    def _read(self) -> dict:
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(
                f"IndexTTS worker exited (code {self.proc.poll()}); see third_party/indextts_worker.log"
            )
        return json.loads(line)

    def synth(
        self, text: str, ref: Path, out: Path, duration_factor: float = 1.0, emo_ref: Path | None = None
    ) -> None:
        req = {
            "id": next(self._ids),
            "text": text,
            "ref": str(ref.resolve()),
            "out": str(out.resolve()),
            "emo_ref": str(emo_ref.resolve()) if emo_ref else None,
            "lang": self.lang,
            "duration_factor": round(float(duration_factor), 3),
            "max_mel_tokens": int(MEL_TOKENS_PER_SEC * max_speech_sec(text)),
            "emo_alpha": self.emo_alpha,
            "num_beams": self.num_beams,
        }
        with self._lock:
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            rep = self._read()
        if not rep.get("ok"):
            raise RuntimeError(f"IndexTTS: {rep.get('error')}")

    def close(self) -> None:
        try:
            self.proc.stdin.write('{"cmd":"quit"}\n')
            self.proc.stdin.flush()
            self.proc.wait(10)
        except Exception:
            self.proc.kill()
        finally:
            self._stderr_log.close()


class MockTTS:
    """Speech-like placeholder audio (~0.14 s per character), for testing without a GPU."""

    def synth(
        self, text: str, ref: Path, out: Path, duration_factor: float = 1.0, emo_ref: Path | None = None
    ) -> None:
        from .ff import write_wav

        time.sleep(float(os.environ.get("YTJP_MOCK_TTS_DELAY", "0")))  # simulate a slow GPU in tests
        sr = 22050
        dur = max(0.3, 0.14 * len(text) * duration_factor)
        t = np.arange(int(sr * dur)) / sr
        f0 = 140 + 30 * np.sin(2 * np.pi * 0.7 * t)
        env = 0.5 * (1 + np.sin(2 * np.pi * 4.5 * t)) * np.minimum(1, np.minimum(t, dur - t) * 20)
        wav = 0.2 * env * np.sin(2 * np.pi * np.cumsum(f0) / sr)
        write_wav(out, wav.astype(np.float32)[:, None], sr)

    def close(self) -> None:
        pass


def make_tts(cfg):
    if cfg.tts.backend == "mock":
        return MockTTS()
    t = cfg.tts
    return IndexTTSClient(cfg.indextts_dir, t.lang, t.use_bf16, t.emo_alpha, t.num_beams)
