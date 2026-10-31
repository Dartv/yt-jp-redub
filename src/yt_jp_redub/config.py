"""Settings: defaults, overridden by config.toml, plus secrets from .env."""

from __future__ import annotations

import io
import ipaddress
import json
import math
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[2]

DEFAULTS: dict = {
    "server": {
        "host": "0.0.0.0",
        "port": 8767,
        "ws_port": 8766,
        "public_host": "",
        "unload_after_min": 5,
        "low_vram": "auto",
    },
    "storage": {"data_dir": "data", "cap_gb": 50},
    "download": {"max_height": 1080, "cookies_file": ""},
    "asr": {"model": "nemo-parakeet-tdt-0.6b-v2", "device": "cuda", "use_vocals": False},
    "segment": {
        "max_line_chars": 120,
        "max_line_sec": 9.0,
        "pause_split_sec": 0.5,
        "chunk_sec": 60,
        "first_chunk_sec": 15,
    },
    "translate": {
        "backend": "openai",
        "batch_lines": 40,
        "context_lines": 16,
        "rpm": 0,  # 0: the provider's known limit (translate.KNOWN_LIMITS), else none
        "tpm": 0,
        "max_retries": 20,
        "temperature": 0.4,
        "concurrency": 3,
        "reasoning_effort": "none",
    },
    "separate": {"backend": "demucs", "model": "htdemucs", "device": "cuda"},
    "tts": {
        "backend": "indextts",
        "indextts_dir": "third_party/index-tts",
        "lang": "JA",
        "max_speedup": 1.25,
        "max_slowdown": 1.2,
        "group_speakers": True,
        "speaker_sim": 0.6,
        "group_gap_sec": 0.4,
        "group_max_sec": 12.0,
        "min_ref_sec": 3.0,
        "use_bf16": True,
        "emo_alpha": 1.0,
        "num_beams": 1,
    },
    "stream": {"lookahead_chunks": 3, "hold_sec": 20, "video_segment_sec": 6},
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
    config_file: Path = ROOT / "config.toml"
    env_file: Path = ROOT / ".env"

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
    secrets = Secrets(
        llm_base_url=env.get("LLM_BASE_URL") or Secrets.llm_base_url,
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_model=env.get("LLM_MODEL") or Secrets.llm_model,
        llm_system_prompt=_bool(env.get("LLM_SYSTEM_PROMPT")),
    )
    return Config(**{k: _ns(v) for k, v in data.items()}, secrets=secrets, config_file=cfg_file)


# ---------------------------------------------------------------------------- the settings dialog
class BadSetting(ValueError):
    def __init__(self, name: str, message: str):
        super().__init__(message)
        self.name = name


def _number(lo, hi, whole=False):
    def parse(v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise ValueError("Enter a number") from None
        if not (math.isfinite(x) and lo <= x <= hi) or (whole and not x.is_integer()):
            raise ValueError(f"Enter a {'whole ' if whole else ''}number from {lo} to {hi}")
        return int(x) if x.is_integer() else x

    return parse


def _choice(*options):
    def parse(v):
        for o in options:
            if str(o).lower() == str(v).lower():
                return o
        raise ValueError("Pick one of the options")

    return parse


def _url(v):
    v = str(v).strip()
    if not re.fullmatch(r"https?://\S+", v):
        raise ValueError("Enter an address that starts with http:// or https://")
    return v


def _text(v):
    v = str(v).strip()
    if not v or "\n" in v or "\r" in v:
        raise ValueError("Fill this in")
    return v


def _file(v):
    v = str(v).strip()
    if v and not Path(v).is_file():
        raise ValueError(f"There's no file at {v}")
    return v


# what the library's settings dialog edits: name -> (config.toml section, or "env" for .env; key; parser)
SETTINGS = {
    "llm_base_url": ("env", "LLM_BASE_URL", _url),
    "llm_model": ("env", "LLM_MODEL", _text),
    "llm_api_key": ("env", "LLM_API_KEY", _text),
    "max_height": ("download", "max_height", _choice(480, 720, 1080, 1440, 2160)),
    "cookies_file": ("download", "cookies_file", _file),
    "cap_gb": ("storage", "cap_gb", _number(1, 100_000)),
    "max_speedup": ("tts", "max_speedup", _number(1, 2)),
    "max_slowdown": ("tts", "max_slowdown", _number(1, 2)),
    "low_vram": ("server", "low_vram", _choice("auto", True, False)),
    "unload_after_min": ("server", "unload_after_min", _number(0, 1440)),
    "port": ("server", "port", _number(1024, 65535, whole=True)),
}


def _target(cfg: Config, name: str) -> tuple[object, str]:
    section, key, _ = SETTINGS[name]
    return (cfg.secrets, name) if section == "env" else (getattr(cfg, section), key)


def current_settings(cfg: Config) -> dict:
    """The dialog's values. The API key only as whether one is set: it never goes to a browser."""
    out = {name: getattr(*_target(cfg, name)) for name in SETTINGS}
    out["llm_api_key"] = bool(out["llm_api_key"])
    out["low_vram"] = str(out["low_vram"]).lower()
    return out


def needs_api_key(cfg: Config) -> bool:
    """No API key yet for a hosted translation provider (a local one, like Ollama, takes none):
    the library then opens the settings with a setup guide."""
    return (
        not cfg.secrets.llm_api_key
        and cfg.translate.backend != "mock"
        and not local_url(cfg.secrets.llm_base_url)
    )


def local_url(url: str) -> bool:
    """On this PC or the home network (Ollama, LM Studio)."""
    host = urlsplit(url.strip()).hostname or ""
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def save_settings(cfg: Config, values: dict) -> list[str]:
    """Checks the dialog's values, writes the changed ones into config.toml and .env (their comments
    stay) and applies them to cfg. Returns the changed names; raises BadSetting, writing nothing."""
    new = {}
    for name, v in values.items():
        if name not in SETTINGS:
            raise BadSetting(name, "Unknown setting")
        if name == "llm_api_key" and not v:
            continue  # the dialog doesn't have the saved key: empty keeps it
        try:
            v = SETTINGS[name][2](v)
        except ValueError as e:
            raise BadSetting(name, str(e)) from None
        if v != getattr(*_target(cfg, name)):
            new[name] = v
    toml = {SETTINGS[n][:2]: v for n, v in new.items() if SETTINGS[n][0] != "env"}
    env = {SETTINGS[n][1]: v for n, v in new.items() if SETTINGS[n][0] == "env"}
    writes = []
    if toml:  # without a config.toml yet, it starts as the commented example
        text = _read(cfg.config_file) if cfg.config_file.exists() else _read(ROOT / "config.example.toml")
        writes.append((cfg.config_file, _toml_set(text, toml)))
    if env:
        writes.append((cfg.env_file, _env_set(_read(cfg.env_file) if cfg.env_file.exists() else "", env)))
    for path, text in writes:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, path)
    for name, v in new.items():
        setattr(*_target(cfg, name), v)
    return list(new)


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig")  # bytes: the file's line endings stay as they are


def _toml_set(text: str, changes: dict) -> str:
    """text with each (section, key) set to its value, in place, keeping comments in their column."""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    for (section, key), value in changes.items():
        v = json.dumps(value, ensure_ascii=False)  # its strings, numbers and booleans are TOML's too
        head = next((i for i, line in enumerate(lines) if re.match(rf"\s*\[{section}\]\s*(#|$)", line)), None)
        if head is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += eol
            lines += [eol, f"[{section}]{eol}"]
            head = len(lines) - 1
        end = next((i for i in range(head + 1, len(lines)) if lines[i].lstrip()[:1] == "["), len(lines))
        for i in range(head + 1, end):
            m = re.match(
                rf"""(\s*{key}\s*=\s*)("(?:[^"\\]|\\.)*"|'[^']*'|[^\s#]+)(\s*)(.*)""", lines[i], re.S
            )
            if m:
                gap = " " * max(1, len(m[2]) + len(m[3]) - len(v)) if m[4].startswith("#") else m[3]
                lines[i] = m[1] + v + gap + m[4]
                break
        else:
            lines.insert(head + 1, f"{key} = {v}{eol}")
    out = "".join(lines)
    try:
        data = tomllib.loads(out)
    except tomllib.TOMLDecodeError:
        data = None
    if data is None or any(data[s][k] != v for (s, k), v in changes.items()):
        raise BadSetting("", "config.toml has a layout the settings can't edit; edit it by hand")
    return out


def _env_set(text: str, changes: dict) -> str:
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += eol
    for key, value in changes.items():
        if re.search(r"[\s#'\"\\]", value):  # quoted, a value is read literally
            if "'" in value:
                raise BadSetting(key.lower(), "Can't contain both ' and spaces or quotes")
            value = f"'{value}'"
        i = next((i for i, line in enumerate(lines) if re.match(rf"\s*(export\s+)?{key}\s*=", line)), None)
        if i is None:
            lines.append(f"{key}={value}{eol}")
        else:
            lines[i] = f"{key}={value}{eol}"
    out = "".join(lines)
    got = dotenv_values(stream=io.StringIO(out))
    if any(got.get(k) != v for k, v in changes.items()):
        raise BadSetting("", ".env has a layout the settings can't edit; edit it by hand")
    return out
