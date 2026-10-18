"""Settings: defaults, overridden by config.toml, plus secrets from .env."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

DEFAULTS: dict = {
    "server": {"host": "0.0.0.0", "port": 8765, "ws_port": 8766, "public_host": ""},
    "storage": {"data_dir": "data", "cap_gb": 50},
    "download": {"max_height": 1080, "cookies_file": ""},
    "asr": {"model": "nemo-parakeet-tdt-0.6b-v2", "device": "cuda", "use_vocals": False},
    "segment": {"max_line_chars": 120, "max_line_sec": 9.0, "pause_split_sec": 0.5, "chunk_sec": 60},
    "translate": {
        "backend": "openai", "batch_lines": 40, "context_lines": 8, "rpm": 25, "tpm": 14000,
        "max_retries": 8, "temperature": 0.4,
    },
    "separate": {"backend": "demucs", "model": "htdemucs", "device": "cuda"},
    "tts": {
        "backend": "indextts", "indextts_dir": "third_party/index-tts", "lang": "JA",
        "max_speedup": 1.5, "min_ref_sec": 3.0, "use_bf16": True, "emo_alpha": 1.0,
    },
    "stream": {"head_start_sec": 120, "lookahead_chunks": 3, "hold_sec": 20, "video_segment_sec": 6},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def _ns(d: dict) -> SimpleNamespace:
    return SimpleNamespace(**{k: _ns(v) if isinstance(v, dict) else v for k, v in d.items()})


def _bool(s: str | None) -> bool:
    return (s or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Secrets:
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_api_key: str = ""
    llm_model: str = "gemma-4-31b-it"
    llm_system_prompt: bool = False
    telegram_bot_token: str = ""
    telegram_allowed_user_id: int | None = None


@dataclass
class Config:
    server: SimpleNamespace
    storage: SimpleNamespace
    download: SimpleNamespace
    asr: SimpleNamespace
    segment: SimpleNamespace
    translate: SimpleNamespace
    separate: SimpleNamespace
    tts: SimpleNamespace
    stream: SimpleNamespace
    secrets: Secrets = field(default_factory=Secrets)

    @property
    def data_dir(self) -> Path:
        p = Path(self.storage.data_dir)
        return p if p.is_absolute() else ROOT / p

    @property
    def videos_dir(self) -> Path:
        return self.data_dir / "videos"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db.sqlite"

    @property
    def indextts_dir(self) -> Path:
        p = Path(self.tts.indextts_dir)
        return p if p.is_absolute() else ROOT / p


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    data = DEFAULTS
    cfg_file = Path(path) if path else ROOT / "config.toml"
    if cfg_file.exists():
        with open(cfg_file, "rb") as f:
            data = _merge(data, tomllib.load(f))
    if overrides:
        data = _merge(data, overrides)
    env = os.environ
    uid = env.get("TELEGRAM_ALLOWED_USER_ID", "").strip()
    secrets = Secrets(
        llm_base_url=env.get("LLM_BASE_URL") or Secrets.llm_base_url,
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_model=env.get("LLM_MODEL") or Secrets.llm_model,
        llm_system_prompt=_bool(env.get("LLM_SYSTEM_PROMPT")),
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_allowed_user_id=int(uid) if uid.lstrip("-").isdigit() else None,
    )
    return Config(**{k: _ns(v) for k, v in data.items()}, secrets=secrets)
