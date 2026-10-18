"""The yt-dlp standalone binary, kept up to date by the app itself.

YouTube changes break old yt-dlp versions often, so instead of a pinned Python package we keep
the official release binary in bin/, let it self-update (`yt-dlp -U`, which verifies the release
checksums) at most once a day, and force an update whenever a download fails.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..config import ROOT

log = logging.getLogger(__name__)
BIN_DIR = ROOT / "bin"
RELEASES = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
CHECK_EVERY = 24 * 3600
_lock = threading.Lock()


def _asset() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "yt-dlp.exe" if machine in ("amd64", "x86_64") else "yt-dlp_arm64.exe"
    if sys.platform == "darwin":
        return "yt-dlp_macos"
    return "yt-dlp_linux_aarch64" if machine in ("aarch64", "arm64") else "yt-dlp_linux"


def binary() -> Path:
    return BIN_DIR / _asset()


def _stamp() -> Path:
    return BIN_DIR / ".yt-dlp-checked"


def ensure() -> Path:
    """Downloads the binary on first use."""
    exe = binary()
    with _lock:
        if not exe.exists():
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            log.info("Downloading yt-dlp (%s)", exe.name)
            tmp = exe.with_suffix(".part")
            urllib.request.urlretrieve(RELEASES + exe.name, tmp)
            tmp.chmod(tmp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            os.replace(tmp, exe)
            _stamp().write_text(str(time.time()))
    return exe


def version() -> str:
    return run(["--version"]).stdout.strip()


def update(force: bool = False) -> None:
    """Self-update when the last check is over a day old (or when forced)."""
    exe = ensure()
    try:
        last = float(_stamp().read_text())
    except (FileNotFoundError, ValueError):
        last = 0.0
    if not force and time.time() - last < CHECK_EVERY:
        return
    with _lock:
        p = subprocess.run(
            [str(exe), "-U"], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        _stamp().write_text(str(time.time()))
    msg = (p.stdout + p.stderr).strip().splitlines()
    log.info("yt-dlp update: %s", msg[-1] if msg else p.returncode)


def run(args: list[str], on_line: Callable[[str], None] | None = None) -> subprocess.CompletedProcess:
    """Runs yt-dlp; streams stdout lines to on_line when given."""
    cmd = [str(ensure()), "--no-warnings", *args]
    if on_line is None:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    ) as p:
        for line in p.stdout:
            on_line(line.rstrip())
        err = p.stderr.read()
    return subprocess.CompletedProcess(cmd, p.returncode, "", err)


def info(url: str, extra: list[str]) -> dict:
    p = run(["-J", "--no-playlist", *extra, url])
    if p.returncode != 0:
        raise DownloadError(p.stderr.strip()[-800:] or "yt-dlp failed")
    return json.loads(p.stdout)


class DownloadError(RuntimeError):
    pass
