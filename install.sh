#!/bin/sh
# Linux and macOS: installs git, ffmpeg and uv if they're missing, then runs `yt-jp-redub setup`,
# which downloads IndexTTS, the models and yt-dlp. Safe to run again.
set -eu
cd "$(dirname "$0")"

missing=""
for tool in git ffmpeg; do
  command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
if [ -n "$missing" ]; then
  echo "Installing$missing…"
  if [ "$(uname)" = Darwin ]; then
    command -v brew >/dev/null 2>&1 || { echo "Install Homebrew first (https://brew.sh), then run this again."; exit 1; }
    brew install $missing
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y $missing
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y $missing || { echo "On Fedora, ffmpeg comes from RPM Fusion: https://rpmfusion.org"; exit 1; }
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm $missing
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper install -y $missing
  else
    echo "Install$missing with your package manager, then run this again."
    exit 1
  fi
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
fi

uv run yt-jp-redub setup
