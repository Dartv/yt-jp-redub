# yt-jp-redub — Plan

Self-hosted tool that dubs any YouTube video (including stream VODs) into Japanese with emotion-preserving TTS, so Japanese learners can watch the Western content they already enjoy. Everything runs locally and for free on the user's PC; the phone is just a remote and a screen.

Status: planning complete (2026-10-18). Implemented 2026-10-18; see README for usage.

**Built as planned:** yt-dlp download → Parakeet ASR → segmentation → OpenAI-compatible translation → Demucs → IndexTTS-2.5 (native on Windows) → time-fit → HLS streaming with seek-aware chunk priority, held segments, head start and catch-up overlay. Also built: the player (ja/en audio, built-in subtitles), the library, `add`, the bookmarklet, LAN IP discovery, the storage cap, resume after restart, asbplayer WebSocket on PC and `<track>`/page events for Android, and the offline `process` → .mkv.

**Changed deliberately** (by request or after measurement):
- **Telegram bot:** removed; the paste box covers it.
- **Distribution:** no `setup.ps1`/`setup.sh` and no autostart. A single CLI (`uv run yt-jp-redub setup|serve`) instead.
- **Speed cap:** 1.25× instead of 1.5×; utterances may also slow down to 1.2×.
- **Speaker grouping:** consecutive sentences from the same speaker are voiced as one utterance (WeSpeaker CAM++).
- **Translation:** gets a per-line character budget so the Japanese fits the timing.
- **yt-dlp:** a self-updating binary in `bin/` instead of a pinned package.
- **ASR:** runs through onnx-asr with Silero VAD.
- **Job state:** lives in SQLite (no `state.json`).

**Not verified yet:**
- Firefox on Android, and asbplayer's extension itself (both protocols were checked against its source, not run against it)
- Linux and macOS
- GitHub publishing (not done)

---

## Goals

- **Seamless:** share a YouTube link, then start watching within a couple of minutes, whatever the video's length.
- **Free:** no paid services. Every model runs locally except translation, which goes through any OpenAI-compatible API (default: Gemma on Google AI Studio's free tier).
- **Built for listening practice:** natural spoken Japanese voiced in each speaker's own voice and emotion, with Japanese and English subtitles that Yomitan and asbplayer can read.
- **Open source:** GPL-3.0, published on GitHub, cross-platform (Windows, Linux, macOS).
- **Mobile and Telegram are optional.** PC-only use must work with neither.

## Non-goals (v1)

- Anki mining with audio and screenshots. Yomitan's own word mining already works.
- Paid TTS or translation backends.
- Docker, a GUI or a tray icon.
- Access from outside the home network. Tailscale and similar can be documented later.
- A browser extension. The bookmarklet covers v1.

---

## User flows

### Android (optional)
1. In the YouTube app, tap Share → Telegram → the bot.
2. The bot replies immediately with a single status message that updates in place (queued → downloading → transcribing → translating → dubbing) and holds a player link with the thumbnail.
3. Tap the link, and Firefox for Android opens the player. Playback starts once the first ~2 minutes are dubbed. Yomitan works on the subtitles, and asbplayer loads the subtitles with one tap.

### PC
Three ways to submit a video, all of which open the player:
- Paste a URL into the box on the player's home page.
- Run `yt-jp-redub add <url>`.
- Click the **"Dub this"** bookmarklet while on a YouTube page. It opens `http://<host>/add?url=…`.

With asbplayer's WebSocket client enabled, subtitles load into asbplayer automatically.

### General rules
- Every submitted link is queued. There are no bot commands.
- Re-submitting a URL that's already processed or in progress returns the existing job with a fresh link. This also takes care of a changed IP.
- Jobs run one at a time, first in, first out, with no length cap.

---

## Pipeline

```
URL ─► yt-dlp ─► ASR (whole video) ─► translate (whole video) ─► subtitles ready
                                                                       │
                       ┌───────────────── per chunk, priority-ordered ─┘
                       ▼
            Demucs (vocals / background) ─► IndexTTS-2.5 per line ─► time-fit ─► mix ─► audio segments
```

| Stage | Tool | Notes |
|---|---|---|
| Download | `yt-dlp` | Up to 1080p (configurable). Prefer H.264 (`avc1`) + AAC (`m4a`) for MSE compatibility. The video is never re-encoded. |
| ASR | NVIDIA **Parakeet TDT 0.6B v2** via an ONNX runtime port (`onnx-asr` or `sherpa-onnx`) | English only, word timestamps. 24-minute limit per pass, so audio is split into ≤20-minute windows at silences. Runs on the original audio so subtitles arrive fast. Running it on Demucs vocals is a config option for music-heavy videos. |
| Segmentation | own code | Groups words into subtitle lines (sentence and pause aware, readable lengths). Groups lines into **dub chunks** of about 60 s, split at silences. |
| Translation | any **OpenAI-compatible API** (default Gemma on Google AI Studio's free tier) | Natural spoken Japanese that matches the speaker's register (casual stays casual). Batches of about 40 lines with preceding lines as context, JSON in and out keyed by line ID, and a check that line counts match. Retries with backoff, with RPM/TPM limits set in config. Gemma on AI Studio rejects system prompts, so instructions are merged into the user message. |
| Source separation | **Demucs** (htdemucs) | Per chunk. The vocals stem provides TTS reference audio, and the background stem (music and sound effects) is kept in the mix. |
| TTS | **IndexTTS-2.5** (Bilibili) | Zero-shot, with timbre and emotion handled separately. Each line uses its **own original vocals** as the voice and emotion reference, so speakers keep their voices with no separate speaker-detection step. References shorter than about 3 s are padded with neighbouring audio from the same time window. Weights are downloaded at setup and never redistributed (Bilibili model license). |
| Time-fit | IndexTTS `duration_factor` | Lines are generated at a pace that fits the original slot, up to 1.5× faster. Anything still too long spills into the following silence and pushes later lines back, so lines never overlap. Native speed control is preferred over ffmpeg time-stretching. |
| Mix | ffmpeg | Japanese voice + background stem → AAC. The original English mix is kept as a second audio rendition. |

### Ordering: subtitles first, then the dub
1. ASR and translation run over the **whole video** first. They are much faster than TTS, and asbplayer needs the full subtitle file in one go.
2. The dub then runs chunk by chunk. While translation is still running, the built-in subtitles show whatever exists so far.
3. Translation of the first chunks is prioritised so the 2-minute head start isn't held up by a long VOD.

### Chunk priority (seek-aware)
- By default, chunks are processed in order.
- The player reports its playhead, and requests for audio segments also act as a signal. The chunk at the playhead plus the next N chunks always go first.
- After a seek past the dubbed region, the worker switches to the new position and fills in the skipped chunks later.

---

## Streaming player

Served by the app's own HTTP server. It's a minimal page; subtitle extras come from asbplayer and Yomitan rather than being rebuilt here.

### Media delivery (proposed, to be validated in milestone 3)
- **HLS VOD playlists played through hls.js**, with every segment listed up front so the full timeline and seek bar are known from the start.
  - Video rendition: fMP4 segments cut from the downloaded file with `-c copy` (keyframe-aligned).
  - Audio renditions: `ja` (dub) and `en` (original), switchable through hls.js audio tracks.
- A `ja` segment that isn't dubbed yet is held server-side (long-poll with a timeout). hls.js retries, and the request doubles as a priority hint for the worker.
- Fallback if alternate-audio switching proves unreliable on Firefox for Android: two variant streams (video+ja and video+en), with the switch reloading at the current time.

### Player behaviour
- Playback starts once 2 minutes past the start point are dubbed. The bot's link arrives immediately, and the page shows "preparing…" and then starts by itself.
- If playback catches up with the dubbed region, it pauses on a "dubbing… ready in ~N s" overlay and resumes automatically.
- Japanese/English audio switch.
- **Built-in subtitles:** Japanese and English toggles, rendered as plain page text so Yomitan can scan them. Tapping the subtitle area pauses playback, and it stays paused until play is pressed. This is the fallback when asbplayer isn't installed, and it covers the time before the full subtitles are ready.
- No furigana or romaji. Yomitan provides readings.

### asbplayer integration
Findings are from asbplayer source at HEAD `62c4d6e`, 2026-10-15:
- **PC:** the app runs a WebSocket server (default port `8766`, configurable). asbplayer's WebSocket client connects to it; the user enables this once in asbplayer's misc settings. When the full subtitles are ready, the app sends `load-subtitles` with the ja/en files once. asbplayer can't append to loaded subtitles, which is why subtitles come first.
- **Android:** asbplayer's WebSocket interface is missing on Firefox for Android. The page instead exposes subtitles through `<track>` elements and asbplayer's page event protocol: it listens for `asbplayer-get-synced-data` and replies with `asbplayer-synced-data` and a list of subtitle URLs. The user taps asbplayer in the extension menu once per video. The protocol is undocumented and may change.
- Subtitle files are served with `Cache-Control: no-store`.
- The asbplayer **Android** add-on is labelled experimental and has no screenshot support. That doesn't matter for v1, which doesn't do mining.

### Library
- The home page has the URL paste box and a grid of processed and in-progress videos, each with a progress indicator.
- Storage cap defaults to 50 GB (configurable). When over the cap, the least recently watched videos are deleted first.

---

## Network and access

- **Default:** the server listens on the LAN, so the phone on the same Wi-Fi can reach it. Setup adds the OS firewall rule.
- **IP auto-discovery:** every generated link uses the PC's current LAN IPv4. Candidates are private IPv4 addresses on interfaces that have a default gateway. Virtual and VPN adapters are excluded (Cloudflare WARP, Tailscale, vEthernet/Hyper-V, VirtualBox, VMware, Docker, WSL). `PUBLIC_HOST` in config overrides the result.
- **No authentication.** Anyone on the same network can open the player and submit videos. The README warns users on shared networks, and `HOST=127.0.0.1` in config limits access to the PC itself.

---

## Telegram bot (optional)

- Enabled only when `TELEGRAM_BOT_TOKEN` is set.
- Only answers the Telegram user ID in `TELEGRAM_ALLOWED_USER_ID` and ignores everyone else.
- Any message containing YouTube URL(s) is queued. There are no commands.
- One status message per job, edited in place as stages advance. The player link and thumbnail are sent right away, and the message notes when the dub is complete.
- Uses polling (no webhook), so no inbound internet port is needed. Telegram holds messages for about 24 h if the PC is off.

---

## Project layout (proposed)

```
yt-jp-redub/
  pyproject.toml            # uv-managed, Python 3.11
  .env.example              # secrets: bot token, allowed user id, LLM base_url/key/model
  config.example.toml       # settings: quality, storage cap, port, speed cap, chunk size…
  setup.ps1 / setup.sh      # install uv + Python, download models, interactive .env wizard, firewall rule
  autostart/                # Task Scheduler XML, systemd unit, launchd plist
  src/yt_jp_redub/
    cli.py                  # serve | add <url> | process <url> (offline, no server)
    server/                 # HTTP API, HLS endpoints, player page, asbplayer WebSocket server, access key
    worker/                 # job queue + chunk scheduler (priority, seek handling)
    pipeline/               # download, asr, segment, translate, separate, tts, timefit, mix, hls
    bot/                    # optional Telegram adapter
    net.py                  # LAN IP discovery
  web/                      # player + library (static; hls.js vendored)
  data/                     # gitignored: videos/<youtube_id>/…, db.sqlite
```

Per-video data: `source.*`, `transcript.json`, `lines.json` (en + ja + timings + chunk IDs), `subs/{ja,en}.vtt`, `chunks/<n>/{vocals,background,dub}.wav`, `hls/…`, `state.json`.

Job and chunk state lives in SQLite, so restarts resume where they left off.

---

## Platforms

| Platform | Support |
|---|---|
| Windows + NVIDIA | Full (primary dev machine: RTX 5080 16 GB, 31 GB RAM). Runs directly on Windows, no WSL. |
| Linux + NVIDIA | Full |
| macOS (Apple Silicon) | "Should work, slower" (PyTorch MPS), community-tested |

- Python 3.11 via `uv`. The system Python (3.14) is too new for most ML libraries.
- RTX 50-series (Blackwell) needs PyTorch builds for CUDA 12.8+.

## Licensing

- Project code: **GPL-3.0**.
- Dependencies: yt-dlp (Unlicense), Demucs (MIT), Parakeet weights (CC-BY-4.0), hls.js (Apache-2.0). All compatible.
- IndexTTS-2.5 weights fall under the Bilibili Model Use License. They are downloaded by setup, never committed, and the README links to the license.
- README disclaimer: downloading YouTube content is for personal use, and the user is responsible for complying with YouTube's terms.

---

## Milestones

Each milestone ends with something runnable.

1. **TTS feasibility.** Take 3–4 emotional lines from a real video and run Demucs → IndexTTS-2.5 with the original line as reference, on Windows. *Done when* the output sounds acceptable to the user and IndexTTS runs directly on Windows. If it doesn't, decide between WSL2 and another TTS before going further.
2. **Offline end-to-end.** `yt-jp-redub process <url>` runs every stage and writes an `.mkv` with ja/en audio and ja/en subtitles. *Done when* a 10-minute video plays correctly in mpv. This milestone measures real-time factors per stage on the 5080 and the translation throughput on the free tier.
3. **Streaming.** Chunk scheduler with seek priority, HLS endpoints with held segments, and the player page with the head start, catch-up overlay, audio switch and built-in subtitles. *Done when* a 1-hour video starts within about 2 minutes on the PC and on Firefox for Android, and seeking ahead works.
4. **Library and network.** Library page, URL paste box, CLI `add`, bookmarklet, IP auto-discovery, storage cap, resume after restart.
5. **asbplayer.** WebSocket server with `load-subtitles` on PC, and the page event protocol plus `<track>` on Android.
6. **Telegram bot.** Optional adapter with an edited status message and the allowed-user filter.
7. **Ship it.** Setup scripts for each OS, autostart configs, README (with BotFather and asbplayer/Yomitan setup), Linux check, macOS best effort, GitHub release.

---

## Risks and unknowns

| Risk | Mitigation |
|---|---|
| IndexTTS-2.5 Japanese quality or emotion transfer is disappointing | Milestone 1 tests this before anything else is built. Alternatives: GPT-SoVITS, CosyVoice, Qwen3-TTS. |
| IndexTTS doesn't run directly on Windows | Fall back to WSL2 for the TTS worker only. |
| TTS slower than real time | Measure in milestone 2. Tune chunk size, batch lines, and raise the head start if needed. |
| Free-tier Gemma rate limits stall long VODs | Translate in priority order, make limits configurable, and allow any OpenAI-compatible endpoint, including a local one. |
| YouTube changes break `yt-dlp` | Keep yt-dlp updated (setup and `serve` check for updates), with optional cookies support. |
| Alternate-audio switching in hls.js unreliable on Firefox for Android | Fallback: two variant streams (see Media delivery). |
| asbplayer's page event protocol is undocumented | Built-in subtitles always work. Pin the tested asbplayer version in the README. |
| Short or overlapping lines give bad TTS references | Pad with neighbouring audio and fall back to the nearest clean line in the same chunk. |

---

## Later (post-v1)

- Anki mining: add the audio clip and screenshot to the last card (asbplayer-style), on PC and Android.
- Browser extension with a "Dub" button on YouTube.
- Japanese difficulty setting for translation.
- Remote access outside the home network (Tailscale docs).
- Offline download for watching away from home.
