# yt-jp-redub

Dub any YouTube video, Twitch VOD or Twitch clip into Japanese, so you can practise listening with the Western content you already watch.

- Each speaker keeps **their own voice and emotion**: every line is voiced by [IndexTTS-2.5](https://github.com/index-tts/index-tts) using that line's original audio as the reference.
- **Japanese and English subtitles** that [Yomitan](https://github.com/yomidevs/yomitan) can scan and [asbplayer](https://github.com/killergerbah/asbplayer) can load.
- **Streaming**: start watching about two minutes after you submit a link, whatever the video's length. Seeking ahead re-prioritises the dub.
- **Free and local**: everything runs on your PC. Translation goes through any OpenAI-compatible API (default: Gemma on Google AI Studio's free tier).
- Your phone is just a remote and a screen. Open the player in Firefox on the same Wi-Fi.

## Requirements

- An NVIDIA GPU with ~10 GB+ VRAM (developed on an RTX 5080 16 GB). Apple Silicon should work, more slowly, and is community-tested.
- Windows, Linux or macOS.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/), `git`, and [`ffmpeg`](https://ffmpeg.org/download.html) on your PATH.
- Optional: [Deno](https://deno.com/) on your PATH makes YouTube downloads more reliable (yt-dlp uses it to solve YouTube's JavaScript challenges).
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
- downloads yt-dlp and the IndexTTS-2.5, Parakeet, Demucs and speaker models (several GB)

At the end it prints the one firewall command needed so your phone can reach the PC.

## Run

```bash
uv run yt-jp-redub serve
```

It prints the library address, for example `http://192.168.1.20:8765/`, and a QR code of it. The address is your PC's LAN IP, detected automatically; VPN and virtual adapters are skipped. Set `public_host` in `config.toml` to override it.

**Phone:** on the same Wi-Fi, point the camera at the QR code (in the terminal, or at the top right of the library page on the PC). Then use the browser's **Add to Home screen** to reach it in one tap next time.

### Adding videos

- **Library page:** paste a link into the box; a pasted YouTube or Twitch VOD/clip link starts dubbing right away. The clipboard button next to it dubs the copied link in one tap on the PC. On a phone the page can't read the clipboard (browsers allow that only over HTTPS), so the button opens the box instead, and the keyboard's suggestion bar offers the copied link: tap it.
- **Bookmarklet (PC):** drag "Dub this" from the library page's help section to your bookmarks bar, then click it on any YouTube video or Twitch VOD/clip page.
- **CLI:** `uv run yt-jp-redub add <url>` queues the video on the running server and opens the player.

Twitch: VODs (`twitch.tv/videos/…`) and clips work; live streams don't. Twitch video is capped at 720p, since streams run for hours. Subscriber-only VODs need `cookies_file` in `config.toml`. A whole VOD downloads before dubbing starts, so a multi-hour stream takes a while to get going.

Jobs run one at a time, first in first out, with no length cap. Re-submitting a link that's already done or in progress just opens it again.

### Watching

The player shows "preparing…" and starts by itself once about 2 minutes past the start point are dubbed. If playback catches up with the dub, it pauses with a "dubbing… ready in ~N s" overlay and resumes on its own.
- **日本語 / English:** switches the audio track.
- **JA / EN:** toggles the built-in subtitles. They're plain page text, so Yomitan can scan them. Tapping the subtitle area pauses playback until you press play.
- **⛶:** fullscreen with subtitles overlaid on the picture. On a phone, turning it sideways goes fullscreen too. In portrait, the video sits on top with large subtitles underneath.
- **⚙:** playback speed (0.5×–1.5×) and subtitle size. Both are remembered on that device.
- The lighter part of the seek bar shows what is already dubbed. In the library, a red line under a thumbnail shows how far you've watched.
- Keyboard: space plays/pauses, ←/→ jump to the previous/next subtitle and ↓ to the start of the current one (as in asbplayer), `a`/`d` skip 5 s, `<`/`>` change the speed, `f` toggles fullscreen.
- Touch: double-tap the left or right side to skip 5 s; swipe left/right for the next/previous subtitle.
- The player remembers where you stopped (on the server, so it carries over between PC and phone) and resumes there. The title links to the original video on YouTube or Twitch at the current time. A failed video has a **Retry** button in the library and the player.
- If audio switching misbehaves on a device, open the player with `?mode=variants` (switching then reloads at the current time).

### Offline

Once a video is fully dubbed, **Download** (top right in the player, ⤓ on a library card) saves it to watch without the server, e.g. on a phone, as three files named like the video:
- `… [JA dub].mp4`: Japanese (default) and English audio, Japanese and English subtitles.
- `… [JA dub].ja.srt` and `… [JA dub].en.srt`: the subtitles as separate files, for players such as asbplayer.

All three start at once. The first time, the browser may ask whether the page may download several files: allow it. When a dub finishes, its Japanese track is joined into one audio file (`dub.m4a`, about 90 MB per hour of video); a download then only packages existing streams, which takes a second or two. Videos dubbed before this feature get their track in the background when `serve` starts.

To watch on a phone:
- **VLC:** open the `.mp4`. It shows both audio tracks and both subtitle tracks. Many built-in gallery players show only the Japanese audio and no subtitles.
- **asbplayer** (subtitle list, line-by-line navigation, Anki mining): once, while online, open [app.asbplayer.dev](https://app.asbplayer.dev) and use the browser's **Add to home screen**; from then on it also works offline ([asbplayer's offline guide](https://docs.asbplayer.dev/docs/guides/offline-usage)). Open it from the home screen and load the `.mp4` and the `.ja.srt` (and `.en.srt` if you want both). It plays the Japanese dub; switching to the English track needs Chrome's "Experimental Web Platform features" flag ([asbplayer's audio track guide](https://docs.asbplayer.dev/docs/guides/audio-track-selection)).

This player page itself doesn't work offline: that needs HTTPS (browsers allow offline web apps only on secure origins), and the server is plain HTTP on your network.

```bash
uv run yt-jp-redub process <url> -o video.mkv
```

Writes an `.mkv` with Japanese and English audio and subtitles, with no server. It also prints how long each stage took.

## Performance

Measured on an RTX 5080 with Gemma 4 31B on AI Studio's free tier, with a 1-hour podcast:
- download 20 s, ASR 30 s for the whole hour
- dubbing ~0.6× real time per chunk (stays ahead of playback once started)
- **playback can start after ~4–5 min**

That start-up time is mostly translation: the free tier takes 60–100 s per batch and often answers HTTP 500, which is retried.
A faster or local OpenAI-compatible model shortens it directly; lowering `head_start_sec` also starts sooner.
The first job after `serve` starts doesn't wait for models: they load in the background at startup.

## asbplayer and Yomitan

Install [Yomitan](https://github.com/yomidevs/yomitan) with a Japanese dictionary, and [asbplayer](https://github.com/killergerbah/asbplayer). Tested with asbplayer at commit `62c4d6e` (2026-10-15).

- **PC:** in asbplayer's settings → Misc, enable the WebSocket client with `ws://127.0.0.1:8766/ws`. When a video's full subtitles are ready and its player page is open, they load into asbplayer automatically. The **asbplayer** button re-sends them.
- **Android (Firefox):** asbplayer's WebSocket client isn't available there. Once subtitles are complete, open the asbplayer extension on the player page and pick the 日本語/English tracks; the page exposes them through `<track>` elements and asbplayer's page protocol. That protocol is undocumented, so the built-in subtitles are the fallback.

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
- **Fitting the timing:** the translator gets a character budget per line, so the Japanese fits the time the English took. Consecutive sentences from the same speaker (matched with a speaker-embedding model) are voiced as one utterance, so they flow instead of being stitched together. Each utterance is paced to fill the original speech: up to 1.25× faster or 1.2× slower, through IndexTTS's `duration_factor`. Anything still too long spills into the following pause.
- **yt-dlp:** the official yt-dlp binary lives in `bin/`. It updates itself at most once a day, and immediately whenever a download fails, since YouTube changes break old versions often.
- **State:** job and chunk state live in SQLite (`data/db.sqlite`), so a restart resumes where it left off.

## Security

There is **no authentication**. Anyone on your network can open the player and submit videos. On shared networks (dorms, cafés), set `host = "127.0.0.1"` in `config.toml` to limit access to this PC.

## Development

```bash
uv sync                              # includes the dev tools (pytest, ruff)
uv run pytest
uv run ruff format && uv run ruff check   # Python formatting and lint
npm install && npm run fmt           # web player formatting with oxfmt (needs Node 20.19+)
uv run yt-jp-redub serve --mock      # mock translation/separation/TTS, no GPU or API key needed
```

Code layout:

```
src/yt_jp_redub/
  cli.py              setup | serve | add | process
  config.py, db.py, models.py, net.py
  pipeline/           one module per stage: download (+ytdlp), asr, segment, translate,
                      separate, speaker, pace, tts, dub, hls, subs, export
  worker/             job queue, translation and dubbing loops, chunk scheduler, status, storage cap
  server/             HTTP API + HLS endpoints, asbplayer WebSocket bridge
  tts_worker/         IndexTTS process (runs in IndexTTS's own environment)
web/                  library and player (plain HTML/CSS/JS, hls.js vendored)
```

## Licenses and disclaimer

- This project: **GPL-3.0** ([LICENSE](LICENSE)).
- Dependencies: yt-dlp (Unlicense, downloaded at runtime), Demucs (MIT), Parakeet TDT 0.6B v2 weights (CC-BY-4.0), WeSpeaker CAM++ (Apache-2.0), hls.js (Apache-2.0, vendored in `web/vendor`), Project Nayuki's QR Code generator (MIT, vendored in `src/yt_jp_redub/_vendor`).
- IndexTTS-2.5 weights fall under the [Bilibili Model Use License](https://huggingface.co/IndexTeam/IndexTTS-2.5). `setup` downloads them; they are never redistributed by this project.
- Downloading YouTube content is for personal use only. You are responsible for complying with YouTube's Terms of Service and copyright law where you live.
