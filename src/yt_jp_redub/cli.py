"""yt-jp-redub CLI: setup | serve | add <url> | process <url>."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from .config import ROOT, load_config

log = logging.getLogger("yt_jp_redub")
INDEXTTS_REPO = "https://github.com/index-tts/index-tts.git"
INDEXTTS_COMMIT = "d9e41aa"  # tested with IndexTTS-2.5 on Windows, 2026-10-18


def _logging(verbose: bool) -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError):  # replaced streams may not support it
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpx2", "httpcore", "huggingface_hub", "aiohttp.access", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        import onnxruntime

        onnxruntime.set_default_logger_severity(3)
    except ImportError:
        pass


# ---------------------------------------------------------------------------- serve
async def _serve(cfg) -> None:
    from aiohttp import web

    from .db import DB
    from .net import public_base
    from .server.app import build_app
    from .server.asbplayer_ws import AsbplayerBridge
    from .worker import Worker

    db = DB(cfg.db_path)
    worker = Worker(cfg, db)
    asb = AsbplayerBridge(worker)
    worker.subs_listeners.append(asb.subs_ready)

    runners = []
    for app, port in ((build_app(cfg, worker, asb), cfg.server.port), (asb.app(), cfg.server.ws_port)):
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, cfg.server.host, port).start()
        runners.append(runner)

    base = public_base(cfg)
    print(
        f"\n  yt-jp-redub is running\n"
        f"  Library:   {base}/\n"
        f"  Local:     http://127.0.0.1:{cfg.server.port}/\n"
        f"  asbplayer: ws://127.0.0.1:{cfg.server.ws_port}/ws\n",
        flush=True,
    )
    if not base.startswith("http://127.0.0.1"):
        _print_qr(base)

    await asyncio.to_thread(public_base, cfg)  # warm the LAN IP cache off the event loop
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, worker.models.preload)  # models load while you browse
    loop.run_in_executor(None, _update_ytdlp)  # YouTube breaks old versions often
    try:
        await worker.run()
    finally:
        await worker.close()
        for r in runners:
            await r.cleanup()


def _print_qr(base: str) -> None:
    """The library's address as a QR code: a phone on the same Wi-Fi opens it with its camera."""
    from . import qr

    code = "\n".join("  " + line for line in qr.terminal(base + "/").splitlines())
    with contextlib.suppress(UnicodeEncodeError):  # output redirected to a file in a legacy encoding
        print(f"  Phone: scan this with the camera (same Wi-Fi)\n\n{code}\n", flush=True)


def _update_ytdlp() -> None:
    from .pipeline import ytdlp

    try:
        ytdlp.update()
        log.info("yt-dlp %s", ytdlp.version())
    except Exception as e:
        log.warning("yt-dlp update check failed: %s", e)


# ---------------------------------------------------------------------------- add
def _add(cfg, url: str, open_browser: bool) -> int:
    req = urllib.request.Request(
        f"http://127.0.0.1:{cfg.server.port}/api/jobs",
        method="POST",
        data=json.dumps({"url": url}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"Error: {json.loads(e.read() or b'{}').get('error', e)}", file=sys.stderr)
        return 1
    except OSError:
        print("The server isn't running. Start it with: yt-jp-redub serve", file=sys.stderr)
        return 1
    print(("Queued: " if data["created"] else "Already known: ") + data["link"])
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{cfg.server.port}/watch/{data['video_id']}")
    return 0


# ---------------------------------------------------------------------------- process (offline)
async def _process(cfg, url: str, out: Path | None) -> int:
    from .db import DB
    from .pipeline.export import write_file
    from .worker import Worker

    db = DB(cfg.db_path)
    worker = Worker(cfg, db)
    vid, _ = worker.submit(url)
    timings: dict[str, float] = {}
    state = {"stage": None, "t": time.time()}

    def on_update(v):
        st = db.get_job(v)["stage"]
        if st != state["stage"]:
            if state["stage"]:
                timings[state["stage"]] = timings.get(state["stage"], 0) + time.time() - state["t"]
            state.update(stage=st, t=time.time())
            log.info("stage: %s", st)

    worker.listeners.append(on_update)
    t0 = time.time()
    try:
        doc = await worker.process(vid)
    finally:
        await worker.close()
    job = db.get_job(vid)
    paths = worker.paths(vid)
    out = out or Path.cwd() / f"{vid}.ja-dub.mkv"
    await asyncio.to_thread(write_file, paths, doc, out, job["title"] or vid)
    worker.cleanup(paths)  # the video stays watchable in the library
    dur = job["duration"] or 1
    print(f"\nWrote {out}")
    print(f"Video length {dur:.0f}s, total {time.time() - t0:.0f}s (RTF {(time.time() - t0) / dur:.2f})")
    for stage, sec in timings.items():
        print(f"  {stage:13s} {sec:7.1f}s  RTF {sec / dur:.2f}")
    return 0


# ---------------------------------------------------------------------------- setup
def _setup(cfg, skip_tts: bool) -> int:
    ok = True
    for tool in ("ffmpeg", "ffprobe", "git"):
        if not shutil.which(tool):
            print(f"✗ {tool} not found on PATH. Install it first (see README).")
            ok = False
    if not ok:
        return 1

    for src, dst in (
        (ROOT / "config.example.toml", ROOT / "config.toml"),
        (ROOT / ".env.example", ROOT / ".env"),
    ):
        if not dst.exists():
            shutil.copy(src, dst)
            print(f"✓ created {dst.name}")
    env_path = ROOT / ".env"
    env = env_path.read_text(encoding="utf-8")
    if sys.stdin.isatty() and ("LLM_API_KEY=\n" in env or env.rstrip().endswith("LLM_API_KEY=")):
        print(
            "\nTranslation needs an API key. Default: Gemma on Google AI Studio's free tier.\n"
            "Get one at https://aistudio.google.com/apikey (or leave empty and edit .env later)."
        )
        try:
            key = input("LLM API key: ").strip()
        except EOFError:  # no terminal after all (Windows reports NUL as one)
            key = ""
            print()
        if key:
            env_path.write_text(env.replace("LLM_API_KEY=", f"LLM_API_KEY={key}", 1), encoding="utf-8")
            print("✓ saved to .env")

    if not skip_tts:
        d = cfg.indextts_dir
        uv = os.environ.get("UV") or shutil.which("uv")  # `uv run` tells us where it is
        if not uv:
            print("✗ uv not found on PATH (https://docs.astral.sh/uv/)")
            return 1
        if not (d / ".git").exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", INDEXTTS_REPO, str(d)], check=True)
            subprocess.run(["git", "-C", str(d), "checkout", INDEXTTS_COMMIT], check=True)
        print("Installing the IndexTTS environment (several GB, takes a while)…")
        subprocess.run([uv, "sync", "--python", "3.11"], cwd=d, check=True)
        if not (d / "checkpoints" / "gpt.pth").exists():
            print(
                "Downloading IndexTTS-2.5 weights (~5 GB, Bilibili Model Use License: "
                "https://huggingface.co/IndexTeam/IndexTTS-2.5)…"
            )
            from huggingface_hub import snapshot_download

            snapshot_download("IndexTeam/IndexTTS-2.5", local_dir=str(d / "checkpoints"))
        print("✓ IndexTTS-2.5 ready")

    from .pipeline import ytdlp

    ytdlp.update(force=True)
    print(f"✓ yt-dlp {ytdlp.version()} (updates itself daily)")

    print("Downloading ASR and separation models…")
    from .pipeline import asr

    asr.load(cfg.asr.model, "cpu")
    from demucs.pretrained import get_model

    get_model(cfg.separate.model)
    from .pipeline import speaker

    if not speaker.available():
        return 1
    print("✓ models ready")

    port = cfg.server.port
    print("\nTo let your phone reach this PC, allow the port through the firewall once:")
    if sys.platform == "win32":
        print(
            f'  (admin PowerShell) New-NetFirewallRule -DisplayName "yt-jp-redub" -Direction Inbound '
            f"-Protocol TCP -LocalPort {port} -Action Allow -Profile Private"
        )
    elif sys.platform == "darwin":
        print("  macOS asks the first time the server starts; click Allow.")
    else:
        print(f"  sudo ufw allow {port}/tcp   (only if ufw is enabled)")
    print("\nDone. Start the server with:  uv run yt-jp-redub serve")
    return 0


# ---------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="yt-jp-redub", description="Dub YouTube videos into Japanese.")
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_serve = sub.add_parser("serve", help="run the server (player, library and worker)")
    p_add = sub.add_parser("add", help="queue a video on the running server")
    p_add.add_argument("url")
    p_add.add_argument("--no-open", action="store_true", help="don't open the player in the browser")
    p_proc = sub.add_parser("process", help="dub a video offline into an .mkv (no server)")
    p_proc.add_argument("url")
    p_proc.add_argument("-o", "--output", type=Path)
    p_setup = sub.add_parser("setup", help="install IndexTTS, download models, create config files")
    p_setup.add_argument("--skip-tts", action="store_true")
    for p in (p_serve, p_proc):
        p.add_argument(
            "--mock", action="store_true", help="use mock translation/separation/TTS (for testing)"
        )
    args = ap.parse_args(argv)
    _logging(args.verbose)

    overrides = None
    if getattr(args, "mock", False):
        overrides = {
            "translate": {"backend": "mock"},
            "separate": {"backend": "mock"},
            "tts": {"backend": "mock"},
        }
    cfg = load_config(args.config, overrides)

    if args.cmd == "serve":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_serve(cfg))
        return 0
    if args.cmd == "add":
        return _add(cfg, args.url, not args.no_open)
    if args.cmd == "process":
        return asyncio.run(_process(cfg, args.url, args.output))
    if args.cmd == "setup":
        return _setup(cfg, args.skip_tts)
    return 1


if __name__ == "__main__":
    sys.exit(main())
