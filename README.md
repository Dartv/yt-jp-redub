# yt-jp-redub

Dub YouTube videos, Twitch VODs and Twitch clips into Japanese, so you can practise listening with the content you already watch.

- Each speaker keeps their own voice and tone. [IndexTTS-2.5](https://github.com/index-tts/index-tts) voices every line, using that speaker's original audio as the reference.
- Japanese and English subtitles that [Yomitan](https://github.com/yomidevs/yomitan) can scan and [asbplayer](https://github.com/killergerbah/asbplayer) can load.
- It streams. Playback starts as soon as the dub can stay ahead of you, usually within a minute with a fast translation API, and seeking ahead moves the dub to where you are.
- Translation goes through any OpenAI-compatible API;
- Watch on the PC, or on your phone over the same Wi-Fi.

## Requirements

- An NVIDIA GPU with 8 GB of VRAM or more (tested on an RTX 5080 16 GB). Apple Silicon should work.
- Windows, Linux or macOS.
- About 30 GB of disk for models and environments, plus space for videos (capped at 50 GB by default).
- Optional: [Deno](https://deno.com/) on your PATH makes YouTube downloads more reliable (yt-dlp uses it to solve YouTube's JavaScript challenges).

## Install

Download or clone this repository, then start it:

- Windows: double-click `start.bat`.
- macOS: double-click `start.command`. If macOS refuses to open it, right-click it and choose Open.
- Linux: run `./start.sh` in a terminal.

The first time, it says what it will install and how much space that takes (about 30 GB), asks before going ahead, installs everything (below), then starts the server and opens the library in your browser. After that it just starts.

To install without starting, run the installer for your system in a terminal in the repository's folder.

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Linux and macOS:

```bash
sh install.sh
```

The installer adds `git`, `ffmpeg` and [`uv`](https://docs.astral.sh/uv/) if they're missing (winget on Windows, Homebrew on macOS, your package manager on Linux), then runs `uv run yt-jp-redub setup`, which:
- creates `config.toml`
- asks for your translation API key (get a free one at <https://aistudio.google.com/apikey>)
- installs IndexTTS in its own environment under `third_party/`
- downloads yt-dlp and the IndexTTS-2.5, Parakeet, Demucs and speaker models (several GB)

At the end it prints the one firewall command your phone needs to reach the PC. Running the installer again is safe; it skips what is already there.

If you'd rather install `git`, `ffmpeg` and `uv` yourself, run `uv run yt-jp-redub setup` directly.

## Run

Double-click `start.bat` or `start.command`, or run `./start.sh`. From a terminal, `uv run yt-jp-redub serve` does the same without opening the browser (add `--open` for that).

The server prints the library address, for example `http://192.168.1.20:8767/`, and a QR code of it. The address is your PC's LAN IP, detected automatically; VPN and virtual adapters are skipped. Set `public_host` in `config.toml` to override it.

On your phone, join the same Wi-Fi and point the camera at the QR code (in the terminal, or at the top right of the library page on the PC). Add the page to your home screen to open it in one tap next time.

### Adding videos

- Library page: paste a link into the box and dubbing starts right away. On the PC, the clipboard button next to it dubs the link you copied in one click. On a phone, tap the box: the keyboard's suggestion bar offers the link you copied, and tapping it starts the dub (so does long-pressing the box and choosing Paste). Phone browsers don't let the page read the clipboard over plain HTTP, so the phone page has no clipboard button.
- Bookmarklet (PC): drag "Dub this" from the library's "Other ways to add videos" to your bookmarks bar, then click it on any YouTube video or Twitch VOD or clip page.
- Command line: `uv run yt-jp-redub add <url>` queues the video on the running server and opens the player.

Twitch VODs (`twitch.tv/videos/…`) and clips work; live streams don't. Twitch video is capped at 720p, since streams run for hours. Subscriber-only VODs need a cookies file (in the library's settings). A whole VOD downloads before dubbing starts, so a multi-hour stream takes a while to get going.

Videos are dubbed one at a time, in the order you add them, with no length limit. Adding a link that is already done or in progress opens it again.

### Offline

Once a video is fully dubbed, Download (top right in the player, ⤓ on a library card) saves it to watch without the server, for example on a phone. You get three files named after the video:
- `… [JA dub].mp4`: Japanese (default) and English audio, Japanese and English subtitles.
- `… [JA dub].ja.srt` and `… [JA dub].en.srt`: the subtitles as separate files, for players such as asbplayer.

To watch on a phone:
- VLC: open the `.mp4`. It shows both audio tracks and both subtitle tracks. Many built-in gallery players show only the Japanese audio and no subtitles.
- asbplayer (subtitle list, line-by-line navigation, Anki mining): once, while online, open [app.asbplayer.dev](https://app.asbplayer.dev) and add it to your home screen; from then on it also works offline ([asbplayer's offline guide](https://docs.asbplayer.dev/docs/guides/offline-usage)). Open it from the home screen and load the `.mp4` and the `.ja.srt` (and `.en.srt` if you want both). It plays the Japanese dub; switching to the English track needs Chrome's "Experimental Web Platform features" flag ([asbplayer's audio track guide](https://docs.asbplayer.dev/docs/guides/audio-track-selection)).

The player page itself doesn't work offline. Browsers allow offline web apps only over HTTPS, and the server is plain HTTP on your network.

```bash
uv run yt-jp-redub process <url> -o video.mkv
```

This writes an `.mkv` with Japanese and English audio and subtitles, with no server, and prints how long each stage took.

## How it works

```
URL ─► yt-dlp ─► Parakeet ASR ─► segment ─► translate (whole video) ─► subtitles
                                                 │
                     per chunk (15 s at first, growing to 60 s), playhead first
                                                 ▼
                    Demucs ─► IndexTTS-2.5 per line ─► time-fit ─► mix ─► HLS audio
```

- Download: the video is downloaded as H.264 and AAC and never re-encoded. It's served as HLS: fMP4 video segments plus one AAC segment per dub chunk, for both the Japanese and English audio tracks.
- Subtitles first: speech recognition and translation run over the whole video first, because asbplayer needs the complete file. The chunks you're about to watch are translated first. A short list of names from the transcript keeps their spelling the same across batches.
- Separation: Demucs splits each chunk into vocals (the voice reference for TTS) and background (music and effects, kept in the mix).
- Timing: the translator gets a character budget per sentence, so the Japanese fits the time the English took, and lines that miss it badly are sent back once to be reworded. Consecutive sentences from the same speaker (matched with a speaker-embedding model) are voiced as one utterance, so they flow instead of being stitched together. Each line starts where the original did, paced to fill the original speech: up to 1.25× faster or 1.2× slower. Speech that still runs long catches up through the following pause.
- yt-dlp: the official binary lives in `bin/`. It updates itself at most once a day, and immediately whenever a download fails, since YouTube changes break old versions often.
- State: job and chunk state live in SQLite (`data/db.sqlite`), so a restart resumes where it left off.

## Development

```bash
uv sync                              # includes the dev tools (pytest, ruff)
uv run pytest
uv run ruff format && uv run ruff check   # Python formatting and lint
npm install && npm run fmt           # web player formatting with oxfmt (needs Node 24+)
uv run yt-jp-redub serve --mock      # mock translation/separation/TTS, no GPU or API key needed
```

## Licenses and disclaimer

- This project: GPL-3.0 ([LICENSE](LICENSE)).
- Dependencies: yt-dlp (Unlicense, downloaded at runtime), Demucs (MIT), Parakeet TDT 0.6B v2 weights (CC-BY-4.0), WeSpeaker CAM++ (Apache-2.0), hls.js (Apache-2.0) and htmx (0BSD), both vendored in `web/vendor`, Project Nayuki's QR Code generator (MIT, vendored in `src/yt_jp_redub/_vendor`).
- IndexTTS-2.5 weights fall under the [Bilibili Model Use License](https://huggingface.co/IndexTeam/IndexTTS-2.5). `setup` downloads them; this project never redistributes them.
- Downloading YouTube content is for personal use only. You are responsible for complying with YouTube's Terms of Service and copyright law where you live.
