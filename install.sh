#!/bin/sh
# Linux and macOS: installs git, ffmpeg and uv if they're missing, then runs `yt-jp-redub setup`,
# which downloads IndexTTS, the models and yt-dlp. Safe to run again.
set -eu
cd "$(dirname "$0")"

free=$(df -Pk . | awk 'NR == 2 { print int($4 / 1048576) }')
cat <<EOF
yt-jp-redub installs into $(pwd)
  - git, ffmpeg and uv, if they're missing
  - two Python environments with PyTorch (~16 GB)
  - IndexTTS-2.5 and the speech recognition and separation models (~13 GB)
It needs about 30 GB of disk${free:+; this drive has $free GB free}.
uv also keeps a download cache in your home folder ('uv cache clean' frees it afterwards).
EOF
if [ -n "$free" ] && [ "$free" -lt 30 ]; then
  echo "That isn't enough: free some space or move this folder first."
fi
if [ -t 0 ]; then  # asked only when someone is at the terminal
  printf "Continue? [Y/n] "
  read -r answer
  case "$answer" in
    "" | [Yy] | [Yy][Ee][Ss]) ;;
    *) echo "Nothing was installed."; exit 1 ;;
  esac
fi

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
