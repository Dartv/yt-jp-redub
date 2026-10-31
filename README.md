# yt-jp-redub

Dub YouTube videos, Twitch VODs and Twitch clips into Japanese, so you can practise listening with the Western content you already watch.

- Each speaker keeps their own voice and tone. [IndexTTS-2.5](https://github.com/index-tts/index-tts) voices every line, using that speaker's original audio as the reference.
- Japanese and English subtitles that [Yomitan](https://github.com/yomidevs/yomitan) can scan and [asbplayer](https://github.com/killergerbah/asbplayer) can load.
- It streams. Playback starts as soon as the dub can stay ahead of you, usually within a minute with a fast translation API, and seeking ahead moves the dub to where you are.
- Everything except translation runs on your PC. Translation goes through any OpenAI-compatible API; the default is Gemma on Google AI Studio's free tier.
- Watch on the PC, or on your phone over the same Wi-Fi.

## Requirements

- An NVIDIA GPU with about 10 GB of VRAM or more (developed on an RTX 5080 16 GB). Apple Silicon should work, more slowly, and is community-tested.
- Windows, Linux or macOS.
- About 15 GB of disk for models and environments, plus space for videos (capped at 50 GB by default).
- Optional: [Deno](https://deno.com/) on your PATH makes YouTube downloads more reliable (yt-dlp uses it to solve YouTube's JavaScript challenges).

## Install

Download or clone this repository, open a terminal in its folder and run the installer for your system.

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Linux and macOS:

```bash
sh install.sh
```

The installer adds `git`, `ffmpeg` and [`uv`](https://docs.astral.sh/uv/) if they're missing (winget on Windows, Homebrew on macOS, your package manager on Linux), then runs `uv run yt-jp-redub setup`, which:
- creates `config.toml` and `.env`
- asks for your translation API key (get a free one at <https://aistudio.google.com/apikey>)
- installs IndexTTS in its own environment under `third_party/`
- downloads yt-dlp and the IndexTTS-2.5, Parakeet, Demucs and speaker models (several GB)

At the end it prints the one firewall command your phone needs to reach the PC. Running the installer again is safe; it skips what is already there.

If you'd rather install `git`, `ffmpeg` and `uv` yourself, run `uv run yt-jp-redub setup` directly.

## Run

```bash
uv run yt-jp-redub serve
```

It prints the library address, for example `http://192.168.1.20:8767/`, and a QR code of it. The address is your PC's LAN IP, detected automatically; VPN and virtual adapters are skipped. Set `public_host` in `config.toml` to override it.

On your phone, join the same Wi-Fi and point the camera at the QR code (in the terminal, or at the top right of the library page on the PC). Add the page to your home screen to open it in one tap next time.

### Adding videos

- Library page: paste a link into the box and dubbing starts right away. On the PC, the clipboard button next to it dubs the link you copied in one click. A phone's browser won't let the page read the clipboard over plain HTTP, so there the button opens the box instead, and the keyboard's suggestion bar offers the copied link: tap it.
- Bookmarklet (PC): drag "Dub this" from the library's "Other ways to add videos" to your bookmarks bar, then click it on any YouTube video or Twitch VOD or clip page.
- Command line: `uv run yt-jp-redub add <url>` queues the video on the running server and opens the player.

Twitch VODs (`twitch.tv/videos/…`) and clips work; live streams don't. Twitch video is capped at 720p, since streams run for hours. Subscriber-only VODs need `cookies_file` in `config.toml`. A whole VOD downloads before dubbing starts, so a multi-hour stream takes a while to get going.

Videos are dubbed one at a time, in the order you add them, with no length limit. Adding a link that is already done or in progress opens it again.

### Watching

While the first part is being dubbed, the player shows what it's doing and how long until it can start ("ready in ~20 s"), then starts by itself. It waits until the dub is projected to stay ahead of playback to the end, or until 2 minutes are dubbed (`head_start_sec`), whichever comes first. If playback catches up with the dub anyway, it pauses with the same countdown and resumes on its own.

- Voice (talking-head icon): 日本語 or EN switches the spoken language.
- Subtitles (captions icon): JA and EN switch each language's subtitles on or off. They're plain page text, so Yomitan can scan them. With a mouse, pointing at a subtitle pauses so you can look words up, and moving off it resumes; clicking or tapping a subtitle pauses until you press play.
- Fullscreen puts the subtitles over the picture. On a phone, turning it sideways does the same. In portrait the video sits at the top, large subtitles right under it, and the controls at the bottom within thumb reach.
- Volume (PC): the speaker button mutes, and hovering it opens a slider. Phones use their volume buttons.
- The gear holds playback speed (0.5× to 1.5×) and subtitle size. These and the volume are remembered on that device.
- The lighter part of the seek bar is what's already dubbed. In the library, a red line under a thumbnail shows how far you've watched.
- Keyboard: space plays and pauses, ← and → jump to the previous or next subtitle and ↓ to the start of the current one (as in asbplayer), `a` and `d` skip 5 s, `<` and `>` change the speed, `m` mutes, `f` toggles fullscreen.
- Touch: double-tap the left or right side to skip 5 s; swipe left or right for the next or previous subtitle.
- The player remembers where you stopped (on the server, so it carries over between PC and phone) and resumes there. The title links to the original video at the current time.
- If a video fails, the player says why and what to do about it (for example when YouTube asks to confirm you're not a bot), with a Retry button and the full error under "Details". The library card shows the same reason.
- If audio switching misbehaves on a device, open the player with `?mode=variants`; switching then reloads at the current time.

### Offline

Once a video is fully dubbed, Download (top right in the player, ⤓ on a library card) saves it to watch without the server, for example on a phone. You get three files named after the video:
- `… [JA dub].mp4`: Japanese (default) and English audio, Japanese and English subtitles.
- `… [JA dub].ja.srt` and `… [JA dub].en.srt`: the subtitles as separate files, for players such as asbplayer.

All three start at once. The first time, the browser may ask whether the page may download several files: allow it. When a dub finishes, its Japanese track is joined into one audio file (`dub.m4a`, about 90 MB per hour of video), so a download only packages existing streams and takes a second or two.

To watch on a phone:
- VLC: open the `.mp4`. It shows both audio tracks and both subtitle tracks. Many built-in gallery players show only the Japanese audio and no subtitles.
- asbplayer (subtitle list, line-by-line navigation, Anki mining): once, while online, open [app.asbplayer.dev](https://app.asbplayer.dev) and add it to your home screen; from then on it also works offline ([asbplayer's offline guide](https://docs.asbplayer.dev/docs/guides/offline-usage)). Open it from the home screen and load the `.mp4` and the `.ja.srt` (and `.en.srt` if you want both). It plays the Japanese dub; switching to the English track needs Chrome's "Experimental Web Platform features" flag ([asbplayer's audio track guide](https://docs.asbplayer.dev/docs/guides/audio-track-selection)).

The player page itself doesn't work offline. Browsers allow offline web apps only over HTTPS, and the server is plain HTTP on your network.

```bash
uv run yt-jp-redub process <url> -o video.mkv
```

This writes an `.mkv` with Japanese and English audio and subtitles, with no server, and prints how long each stage took.

## Performance

On an RTX 5080, a chunk dubs in about 0.6× its length, so once playback starts it stays ahead. Download and speech recognition take under a minute even for an hour-long video.

How soon playback starts depends mostly on the translation API. With xAI's `grok-4.3`, four test videos started playing 24 to 38 s after the link was added. On Google AI Studio's free tier, an earlier version of this tool took 4 to 5 minutes, because the free tier allows 25 requests a minute and often answers HTTP 500, which is retried. Any faster OpenAI-compatible model, including a local one, starts sooner.

The first job after `serve` starts doesn't wait for models: they load in the background at startup.

## asbplayer and Yomitan

Install [Yomitan](https://github.com/yomidevs/yomitan) with a Japanese dictionary, and [asbplayer](https://github.com/killergerbah/asbplayer). Tested with asbplayer at commit `62c4d6e` (2026-10-15).

- PC: in asbplayer's settings under Misc, enable the WebSocket client with `ws://127.0.0.1:8766/ws`. When a video's subtitles are complete and its player page is open, they load into asbplayer automatically; "Send subtitles to asbplayer" under the video sends them again.
- Android (Firefox): asbplayer's WebSocket client isn't available there. Once subtitles are complete, open the asbplayer extension on the player page and pick the 日本語 or English track. The page offers them through `<track>` elements and asbplayer's page protocol. That protocol is undocumented, so the player's own subtitles are the fallback.

## Configuration

`config.toml` holds the settings; [config.example.toml](config.example.toml) lists every option with a comment. The ones you're most likely to change are video quality, the storage cap, ports, the cookies file, and the translation limits. `.env` holds the secrets.

To use another translation provider, set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in `.env` for any OpenAI-compatible endpoint, including local ones like Ollama or LM Studio. Set `LLM_SYSTEM_PROMPT=true` if the model accepts system prompts (Gemma on AI Studio doesn't).

Rate limits are set for you on Google AI Studio's free tier and on xAI; for other providers, set `rpm` and `tpm` under `[translate]` if they enforce limits (HTTP 429 answers are retried either way). Thinking models are asked for no reasoning (`reasoning_effort = "none"`), which answered 4 to 10 times faster than "low" and translated as well in a blind comparison.

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

## Security

There is no authentication. Anyone on your network can open the player and add videos. On shared networks (dorms, cafés), set `host = "127.0.0.1"` in `config.toml` to limit access to this PC. Other websites can't read the library through your browser, and the asbplayer port only listens on this PC.

## Development

```bash
uv sync                              # includes the dev tools (pytest, ruff)
uv run pytest
uv run ruff format && uv run ruff check   # Python formatting and lint
npm install && npm run fmt           # web player formatting with oxfmt (needs Node 24+)
uv run yt-jp-redub serve --mock      # mock translation/separation/TTS, no GPU or API key needed
```

Code layout:

```
src/yt_jp_redub/
  cli.py              setup | serve | add | process
  config.py, db.py, models.py, net.py
  pipeline/           one module per stage: download (+ytdlp), asr, segment, translate,
                      separate, speaker, pace, speakable, tts, dub, hls, subs, export
  worker/             job queue, translation and dubbing loops, chunk scheduler, ETAs, status, storage cap
  server/             HTTP API + HLS endpoints, asbplayer WebSocket bridge
  tts_worker/         IndexTTS process (runs in IndexTTS's own environment)
web/                  library and player (plain HTML/CSS/JS, hls.js vendored)
```

## Licenses and disclaimer

- This project: GPL-3.0 ([LICENSE](LICENSE)).
- Dependencies: yt-dlp (Unlicense, downloaded at runtime), Demucs (MIT), Parakeet TDT 0.6B v2 weights (CC-BY-4.0), WeSpeaker CAM++ (Apache-2.0), hls.js (Apache-2.0, vendored in `web/vendor`), Project Nayuki's QR Code generator (MIT, vendored in `src/yt_jp_redub/_vendor`).
- IndexTTS-2.5 weights fall under the [Bilibili Model Use License](https://huggingface.co/IndexTeam/IndexTTS-2.5). `setup` downloads them; this project never redistributes them.
- Downloading YouTube content is for personal use only. You are responsible for complying with YouTube's Terms of Service and copyright law where you live.
