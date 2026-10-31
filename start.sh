#!/bin/sh
# Linux and macOS: starts yt-jp-redub and opens the library in the browser.
# The first time, it installs everything first (install.sh).
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/yt-jp-redub ] || [ ! -f third_party/index-tts/checkpoints/gpt.pth ]; then
  sh install.sh
fi
PATH="$HOME/.local/bin:$PATH"  # where install.sh puts uv
# uv also brings the dependencies up to date after an update; without it, the venv runs as is
if command -v uv >/dev/null 2>&1; then
  exec uv run yt-jp-redub serve --open
fi
exec .venv/bin/yt-jp-redub serve --open
