# yt-jp-redub

Dub any YouTube video, including stream VODs, into Japanese, so you can practise listening with the Western content you already watch.

- Each speaker keeps **their own voice and emotion**: every line is voiced by [IndexTTS-2.5](https://github.com/index-tts/index-tts) using that line's original audio as the reference.
- **Japanese and English subtitles** that [Yomitan](https://github.com/yomidevs/yomitan) can scan and [asbplayer](https://github.com/killergerbah/asbplayer) can load.
- **Streaming**: start watching about two minutes after you submit a link, whatever the video's length. Seeking ahead re-prioritises the dub.
- **Free and local**: everything runs on your PC. Translation goes through any OpenAI-compatible API (default: Gemma on Google AI Studio's free tier).
- Your phone is just a remote and a screen. Open the player in Firefox on the same Wi-Fi.

## Requirements

- An NVIDIA GPU with ~10 GB+ VRAM (developed on an RTX 5080 16 GB). Apple Silicon should work, more slowly, and is community-tested.
- Windows, Linux or macOS.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/), `git`, and [`ffmpeg`](https://ffmpeg.org/download.html) on your PATH.
- About 15 GB of disk for models and environments, plus space for videos (capped at 50 GB by default).

## Install

```bash
git clone https://github.com/<you>/yt-jp-redub.git
cd yt-jp-redub
uv run yt-jp-redub setup
```

`setup` does the following:
- creates `config.toml` and `.env`
- asks for your translation API key (get a free one at <https://aistudio.google.com/apikey>)
- installs IndexTTS in its own environment under `third_party/`
- downloads the IndexTTS-2.5, Parakeet and Demucs models (several GB)

At the end it prints the one firewall command needed so your phone can reach the PC.

## Run

```bash
uv run yt-jp-redub serve
```

It prints the library address, for example `http://192.168.1.20:8765/`. The address is your PC's LAN IP, detected automatically; VPN and virtual adapters are skipped. Set `public_host` in `config.toml` to override it.

### Adding videos

- **Library page:** paste a link into the box.
- **Bookmarklet (PC):** drag "Dub this" from the library page's help section to your bookmarks bar, then click it on any YouTube video page.
- **CLI:** `uv run yt-jp-redub add <url>` queues the video on the running server and opens the player.
- **Telegram (optional):** see below.

Jobs run one at a time, first in first out, with no length cap. Re-submitting a link that's already done or in progress just opens it again.

### Watching

The player shows "preparing…" and starts by itself once about 2 minutes past the start point are dubbed. If playback catches up with the dub, it pauses with a "dubbing… ready in ~N s" overlay and resumes on its own.
- **日本語 / English:** switches the audio track.
- **JA / EN:** toggles the built-in subtitles. They're plain page text, so Yomitan can scan them. Tapping the subtitle area pauses playback until you press play.
- **⛶:** fullscreen with subtitles.
- The green bar under the video shows which parts are already dubbed.
- If audio switching misbehaves on a device, open the player with `?mode=variants` (switching then reloads at the current time).

### Offline

```bash
uv run yt-jp-redub process <url> -o video.mkv
```

Writes an `.mkv` with Japanese and English audio and subtitles, with no server. It also prints how long each stage took.

## Performance

Measured on an RTX 5080 with Gemma 4 31B on AI Studio's free tier, 1-hour podcast:
- download 20 s, ASR 30 s for the whole hour
- dubbing ~0.55× real time per chunk (stays ahead of playback once started)
- **playback can start after ~4–5 min**

That start-up time is mostly translation: the free tier takes 60–100 s per batch and often answers HTTP 500, which is retried.
A faster or local OpenAI-compatible model shortens it directly; lowering `head_start_sec` also starts sooner.
The first job after `serve` starts doesn't wait for models: they load in the background at startup.

## asbplayer and Yomitan

Install [Yomitan](https://github.com/yomidevs/yomitan) with a Japanese dictionary, and [asbplayer](https://github.com/killergerbah/asbplayer). Tested with asbplayer at commit `62c4d6e` (2026-10-15).

- **PC:** in asbplayer's settings → Misc, enable the WebSocket client with `ws://127.0.0.1:8766/ws`. When a video's full subtitles are ready and its player page is open, they load into asbplayer automatically. The **asbplayer** button re-sends them.
- **Android (Firefox):** asbplayer's WebSocket client isn't available there. Once subtitles are complete, open the asbplayer extension on the player page and pick the 日本語/English tracks; the page exposes them through `<track>` elements and asbplayer's page protocol. That protocol is undocumented, so the built-in subtitles are the fallback.

## Telegram bot (optional)

Useful on Android: share a video from the YouTube app straight to the bot. Links sent while the PC is off wait in Telegram for about 24 h.

1. Message [@BotFather](https://t.me/BotFather) and send `/newbot`. Copy the token.
2. Get your numeric user ID, e.g. from [@userinfobot](https://t.me/userinfobot).
3. In `.env`, set `TELEGRAM_BOT_TOKEN=…` and `TELEGRAM_ALLOWED_USER_ID=…`, then restart `serve`.

The bot only answers that one user. It replies to any message containing YouTube links with one status message per video, edited in place (queued → downloading → transcribing → translating → dubbing → complete), with the player link. It uses polling, so no port needs to be opened to the internet.

## Configuration

`config.toml` holds the settings (see [config.example.toml](config.example.toml) for every option), including:
- video quality
- storage cap
- ports
- speed cap for fitting lines
- chunk size
- head start
- translation rate limits

`.env` holds the secrets.

To use another translation provider, set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` for any OpenAI-compatible endpoint. That includes local ones like Ollama or LM Studio. Set `LLM_SYSTEM_PROMPT=true` if the model accepts system prompts (Gemma on AI Studio doesn't).

## How it works

```
URL ─► yt-dlp ─► Parakeet ASR ─► segment ─► translate (whole video) ─► subtitles
                                                 │
                         per ~60 s chunk, playhead first
                                                 ▼
                    Demucs ─► IndexTTS-2.5 per line ─► time-fit ─► mix ─► HLS audio
```

- **Download:** video is downloaded as H.264 + AAC and never re-encoded. It's served as HLS: fMP4 video segments plus one AAC segment per dub chunk, for both the Japanese and English audio tracks.
- **Subtitles first:** ASR and translation run over the whole video first, because asbplayer needs the complete file. The translation of the chunks you're about to watch goes first.
- **Separation:** Demucs splits each chunk into vocals (used as the TTS reference) and background (music and effects, kept in the mix).
- **Time-fit:** each line is generated at a pace that fits its original slot, up to 1.5× faster (IndexTTS `duration_factor`). Anything still too long spills into the following silence.
- **State:** job and chunk state live in SQLite (`data/db.sqlite`), so a restart resumes where it left off.

## Security

There is **no authentication**. Anyone on your network can open the player and submit videos. On shared networks (dorms, cafés), set `host = "127.0.0.1"` in `config.toml` to limit access to this PC.

## Development

```bash
uv sync --extra test
uv run pytest
uv run yt-jp-redub serve --mock     # mock translation/separation/TTS, no GPU or API key needed
```

## Licenses and disclaimer

- This project: **GPL-3.0** ([LICENSE](LICENSE)).
- Dependencies: yt-dlp (Unlicense), Demucs (MIT), Parakeet TDT 0.6B v2 weights (CC-BY-4.0), hls.js (Apache-2.0, vendored in `web/vendor`).
- IndexTTS-2.5 weights fall under the [Bilibili Model Use License](https://huggingface.co/IndexTeam/IndexTTS-2.5). `setup` downloads them; they are never redistributed by this project.
- Downloading YouTube content is for personal use only. You are responsible for complying with YouTube's Terms of Service and copyright law where you live.
