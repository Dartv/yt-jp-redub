import itertools
import re
import shutil
import struct
import time

import pytest

from yt_jp_redub.models import VID_RE, Chunk, Line, LinesDoc, Word, is_youtube, video_ref
from yt_jp_redub.pipeline import hls, subs
from yt_jp_redub.pipeline.asr import tokens_to_words
from yt_jp_redub.pipeline.segment import lines_to_chunks, words_to_lines
from yt_jp_redub.pipeline.speakable import speakable
from yt_jp_redub.server.cards import cards_html
from yt_jp_redub.worker.eta import DubClock
from yt_jp_redub.worker.scheduler import ChunkScheduler
from yt_jp_redub.worker.status import error_hint


def test_youtube_ids():
    def youtube_id(url):
        return (video_ref(url) or (None,))[0]

    assert youtube_id("https://www.youtube.com/watch?v=TpQlyUjp3vM&t=10") == "TpQlyUjp3vM"
    assert youtube_id("https://youtu.be/TpQlyUjp3vM?si=x") == "TpQlyUjp3vM"
    assert youtube_id("https://www.youtube.com/live/abcdefghijk") == "abcdefghijk"
    assert youtube_id("https://m.youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert youtube_id("https://example.com/") is None
    assert video_ref("https://youtu.be/TpQlyUjp3vM")[1] == "https://www.youtube.com/watch?v=TpQlyUjp3vM"


def test_twitch_ids():
    vod = ("twitch-2283746281", "https://www.twitch.tv/videos/2283746281")
    assert video_ref("https://www.twitch.tv/videos/2283746281?t=1h2m3s") == vod
    assert video_ref("https://m.twitch.tv/videos/2283746281") == vod
    assert video_ref("https://www.twitch.tv/somechannel/v/2283746281") == vod
    assert video_ref("https://player.twitch.tv/?video=v2283746281&parent=x") == vod
    clip = ("twitchclip-Shy-Clip_AbC123", "https://clips.twitch.tv/Shy-Clip_AbC123")
    assert video_ref("https://clips.twitch.tv/Shy-Clip_AbC123") == clip
    assert video_ref("https://www.twitch.tv/somechannel/clip/Shy-Clip_AbC123?filter=clips") == clip
    assert video_ref("https://clips.twitch.tv/embed?clip=Shy-Clip_AbC123&parent=x") == clip
    assert video_ref("https://www.twitch.tv/somechannel") is None  # live channel
    assert video_ref("https://clips.twitch.tv/embed") is None
    for vid, _ in [vod, clip]:
        assert re.fullmatch(VID_RE, vid) and not is_youtube(vid)
    assert re.fullmatch(VID_RE, "TpQlyUjp3vM") and is_youtube("TpQlyUjp3vM")


def test_tokens_to_words():
    words = tokens_to_words([" Y", "es", ",", " s", "ir", "."], [0.0, 0.1, 0.2, 0.3, 0.4, 0.5], 10.0, 11.0)
    assert [w.text for w in words] == ["Yes,", "sir."]
    assert words[0].start == 10.0 and words[1].start == 10.3


def _words(text, start=0.0, step=0.3, pause_after=()):
    out, t = [], start
    for i, w in enumerate(text.split()):
        out.append(Word(w, t, t + step - 0.05))
        t += step + (1.0 if i in pause_after else 0)
    return out


def test_lines_split_on_sentences_and_pauses():
    lines = words_to_lines(
        _words("Yes, sir. I'd like an argument please. Mr. Smith is free", pause_after={6})
    )
    assert [l.en for l in lines] == ["Yes, sir.", "I'd like an argument please.", "Mr. Smith is free"]


def test_short_replies_are_own_lines():
    lines = words_to_lines(_words("No, you didn't. Yes, I did. No. Yes."))
    assert [l.en for l in lines] == ["No, you didn't.", "Yes, I did.", "No.", "Yes."]


def test_long_line_splits():
    lines = words_to_lines(_words(" ".join(["word"] * 60)), max_chars=50, max_sec=100)
    assert all(len(l.en) <= 55 for l in lines)


def test_chunks_tile_timeline():
    lines = [Line(i, i * 5.0, i * 5.0 + 4.0, f"line {i}") for i in range(40)]
    chunks = lines_to_chunks(lines, 205.0, chunk_sec=60)
    assert chunks[0].start == 0 and chunks[-1].end == 205.0
    for a, b in itertools.pairwise(chunks):
        assert a.end == b.start
    assert sorted(i for c in chunks for i in c.line_ids) == list(range(40))
    for c in chunks:  # boundaries fall in gaps, never inside a line
        for i in c.line_ids:
            assert c.start <= lines[i].start and lines[i].end <= c.end


def test_first_chunks_are_short():
    lines = [Line(i, i * 5.0, i * 5.0 + 4.0, f"line {i}") for i in range(60)]
    chunks = lines_to_chunks(lines, 300.0, chunk_sec=60, first_sec=15)
    lengths = [c.duration for c in chunks]
    assert 10 <= lengths[0] < 25 and 25 <= lengths[1] < 40 and all(55 <= x < 70 for x in lengths[2:-1])


def test_chunks_no_speech():
    chunks = lines_to_chunks([], 30.0)
    assert len(chunks) == 1 and chunks[0].end == 30.0


def test_speakable_spells_out_numbers_and_symbols():
    assert speakable("95%の処理") == "九十五パーセントの処理"
    assert speakable("賞金10,000ドル") == "賞金一万ドル"
    assert speakable("３.５倍、2026年") == "三点五倍、二千二十六年"
    assert speakable("十二A号室でAFK") == "十二エー号室でエーエフケー"
    assert speakable("一九六〇年代") == "千九百六十年代"
    assert speakable("Minecraftで") == "Minecraftで"  # words are left to the voice


def test_eta_follows_dub_order_and_translation():
    lines = [Line(i, i * 10.0, i * 10.0 + 5.0, f"l{i}", ja="あ" if i < 2 else None) for i in range(3)]
    doc = LinesDoc(30.0, lines, [Chunk(i, i * 10.0, i * 10.0 + 10, [i]) for i in range(3)])
    clock = DubClock(sec_per_speech=1.0, urgent_sec=5.0, other_sec=20.0)
    eta = clock.ready_in("v", doc, done={0}, order=[0, 1, 2], urgent={0, 1})
    assert 0 not in eta and eta[1] == pytest.approx(5.0)
    assert eta[2] == pytest.approx(25.0)  # waits for its (not urgent) translation, then 5 s of speech
    clock.translating([lines[2]], urgent=True)  # its batch went out as urgent
    assert clock.ready_in("v", doc, done={0}, order=[0, 1, 2], urgent={0, 1, 2})[2] == pytest.approx(10.0)
    clock.sent[2] = (clock.sent[2][0] - 40, True)  # sent 40 s ago: overdue, so not "any moment now"
    assert clock.ready_in("v", doc, done={0}, order=[0, 1, 2], urgent={0, 1, 2})[2] == pytest.approx(
        25.0, abs=0.1
    )
    clock.dubbed(doc, doc.chunks[1], 10.0)  # slower than expected: estimates go up
    assert clock.sec_per_speech > 1.0


def test_empty_translations_count_as_missing():
    from yt_jp_redub.pipeline.translate import _line_texts

    # an empty or null answer would otherwise be saved as translated-but-blank and asked for forever
    assert _line_texts({"1": "はい", "2": "", "3": None, "4": {"ja": "ええ"}}) == {1: "はい", 4: "ええ"}


def test_speakable_reads_huge_numbers_digit_by_digit():
    assert speakable("12345678901234567") == "一二三四五六七八九〇一二三四五六七"


def test_models_unload_once_after_idle(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from yt_jp_redub.config import DEFAULTS, Config, _merge, _ns
    from yt_jp_redub.db import DB
    from yt_jp_redub.worker import Worker

    data = _merge(DEFAULTS, {"storage": {"data_dir": str(tmp_path)}, "server": {"unload_after_min": 0.005}})
    cfg = Config(**{k: _ns(v) for k, v in data.items()})
    w = Worker(cfg, DB(cfg.db_path))
    unloads = []
    w.models = SimpleNamespace(loaded=lambda: not unloads, unload=lambda: unloads.append(1))

    async def idle_for(sec):
        task = asyncio.create_task(w.run())
        await asyncio.sleep(sec)
        task.cancel()

    asyncio.run(idle_for(1.5))
    assert unloads == [1]  # after 0.3 s with nothing queued; then it waits without a timeout


def test_low_vram_stages_take_turns(monkeypatch):
    from yt_jp_redub.config import DEFAULTS, Config, _merge, _ns
    from yt_jp_redub.worker import Models, asr

    data = _merge(DEFAULTS, {"separate": {"backend": "mock"}, "tts": {"backend": "mock"}})
    data["server"]["low_vram"] = True
    m = Models(Config(**{k: _ns(v) for k, v in data.items()}))
    on_gpu = set()
    monkeypatch.setattr(asr, "load", lambda *a: on_gpu.add("asr"))
    monkeypatch.setattr(asr, "unload", lambda: on_gpu.discard("asr"))
    assert m.low_vram
    m.preload("asr")
    assert on_gpu == {"asr"} and not {"tts", "separator"} & m.__dict__.keys()
    m.preload("dub")
    assert not on_gpu and {"tts", "separator"} <= m.__dict__.keys()
    m.preload("asr")
    assert on_gpu == {"asr"} and "tts" not in m.__dict__


def test_library_cards_escape_titles_and_say_where_each_video_is():
    job = {
        "video_id": "abcdefghijk",
        "title": '<img src=x onerror="alert(1)">',
        "duration": 65,
        "stage": "dubbing",
        "label": "Dubbing",
        "error": None,
        "error_hint": None,
        "percent": 42,
        "position": None,
        "chunks_done": 1,
        "download_size": None,
        "ready": False,
        "resume_at": None,
    }
    html = cards_html([job])
    assert "<img" not in html and "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    assert "Dubbing · 42% · watchable" in html and ">1:05<" in html and 'class="dl"' not in html
    done = cards_html([{**job, "stage": "done", "ready": True, "download_size": 2.5e9, "resume_at": 30}])
    assert "Ready · 2.5 GB" in done and 'class="dl"' in done and "watched" in done
    failed = cards_html(
        [{**job, "stage": "error", "label": "Failed", "error_hint": ["The video is private", ""]}]
    )
    assert "Failed · The video is private" in failed and 'hx-post="/api/jobs/abcdefghijk/retry"' in failed
    assert "Paste a YouTube" in cards_html([])


def test_error_hints():
    assert error_hint(None) is None
    what, fix = error_hint("ERROR: [youtube] x: Sign in to confirm you're not a bot")
    assert "bot" in what and "cookies file" in fix
    assert error_hint("weird crash")[0] == "Something went wrong"
    # an API outage says UNAVAILABLE; it's not the video that's unavailable
    assert "translation" in error_hint("translation failed after 6 attempts: HTTP 503 UNAVAILABLE")[0]


def test_scheduler_priority():
    s = ChunkScheduler(lookahead=2)
    assert s.order("v", 5) == [0, 1, 2, 3, 4]
    s.set_playhead("v", 3)
    assert s.order("v", 6) == [3, 4, 5, 0, 1, 2]
    assert s.next("v", 6, done={3}, eligible=lambda c: True) == 4
    s.request("v", 1)
    assert s.order("v", 6)[:4] == [3, 4, 5, 1]
    s.playhead["v"] = (3, time.time() - 99999)  # stale playhead is ignored
    assert s.order("v", 6)[0] in (0, 1)


def test_id3_timestamp():
    tag = hls._id3_timestamp(90000 * 61)
    assert tag[:3] == b"ID3" and tag.endswith(struct.pack(">Q", 90000 * 61))


def test_vtt_and_playlist():
    doc = LinesDoc(20.0, [Line(0, 1.0, 2.0, "Hi.", "やあ。", 0), Line(1, 1.5, 3.0, "Bye.", None, 0)], [])

    doc.chunks = [Chunk(0, 0.0, 12.5, [0, 1]), Chunk(1, 12.5, 20.0, [])]
    v = subs.vtt(doc, "ja")
    assert "やあ。" in v and "Bye" not in v
    en = subs.vtt(doc, "en")
    assert "00:00:01.000 --> 00:00:01.500" in en  # clipped at the next cue
    pl = hls.audio_playlist(doc, "ja")
    assert "#EXT-X-TARGETDURATION:13" in pl and "ja/0001.aac" in pl and pl.strip().endswith("#EXT-X-ENDLIST")


def test_llm_endpoint_normalization():
    from yt_jp_redub.pipeline.translate import normalize_endpoint

    root = "https://generativelanguage.googleapis.com/v1beta/openai/"
    native = "https://generativelanguage.googleapis.com/v1beta/models/gemma-4-31b-it:generateContent"
    assert normalize_endpoint(native, "other") == (root, "gemma-4-31b-it")
    assert normalize_endpoint(root, "m") == (root, "m")
    assert normalize_endpoint("http://localhost:11434/v1/chat/completions", "m") == (
        "http://localhost:11434/v1",
        "m",
    )


def test_llm_endpoint_paste_slips():

    from yt_jp_redub.pipeline.translate import normalize_endpoint

    slip = "LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert normalize_endpoint(slip, "m") == ("https://generativelanguage.googleapis.com/v1beta/openai", "m")
    with pytest.raises(RuntimeError):
        normalize_endpoint("not a url", "m")


def test_linux_interface_parsing(monkeypatch):
    from yt_jp_redub import net

    outputs = {
        ("ip", "-4", "route", "show", "default"): "default via 192.168.1.1 dev wlp2s0 proto dhcp\n",
        ("ip", "-4", "-o", "addr", "show"): (
            "1: lo    inet 127.0.0.1/8 scope host lo\n"
            "2: wlp2s0    inet 192.168.1.23/24 brd 192.168.1.255 scope global wlp2s0\n"
            "3: docker0    inet 172.17.0.1/16 scope global docker0\n"
            "4: tailscale0    inet 100.101.1.2/32 scope global tailscale0\n"
        ),
    }
    monkeypatch.setattr(net, "_run", lambda cmd: outputs.get(tuple(cmd), ""))
    monkeypatch.setattr(net.sys, "platform", "linux")
    assert net._discover() == "192.168.1.23"


def test_macos_interface_parsing(monkeypatch):
    from yt_jp_redub import net

    outputs = {
        ("route", "-n", "get", "default"): "   route to: default\n  interface: en0\n",
        ("ifconfig",): (
            "lo0: flags=8049<UP,LOOPBACK>\n\tinet 127.0.0.1 netmask 0xff000000\n"
            "en0: flags=8863<UP,BROADCAST>\n\tinet 10.0.0.42 netmask 0xffffff00 broadcast 10.0.0.255\n"
            "utun3: flags=8051<UP>\n\tinet 10.8.0.2 --> 10.8.0.1 netmask 0xffffff00\n"
        ),
    }
    monkeypatch.setattr(net, "_run", lambda cmd: outputs.get(tuple(cmd), ""))
    monkeypatch.setattr(net.sys, "platform", "darwin")
    assert net._discover() == "10.0.0.42"


def test_ytdlp_release_asset_per_platform(monkeypatch):
    from yt_jp_redub.pipeline import ytdlp

    for plat, machine, asset in (
        ("win32", "AMD64", "yt-dlp.exe"),
        ("darwin", "arm64", "yt-dlp_macos"),
        ("linux", "x86_64", "yt-dlp_linux"),
        ("linux", "aarch64", "yt-dlp_linux_aarch64"),
    ):
        monkeypatch.setattr(ytdlp.sys, "platform", plat)
        monkeypatch.setattr(ytdlp.platform, "machine", lambda m=machine: m)
        assert ytdlp._asset() == asset


def _translation_worker(tmp_path, translator, max_retries=5, concurrency=2):
    from types import SimpleNamespace

    from yt_jp_redub.models import VideoPaths

    lines = [Line(i, i * 2.0, i * 2.0 + 1.5, f"line {i}") for i in range(30)]
    chunks = [Chunk(c, c * 20.0, c * 20.0 + 20, list(range(c * 10, c * 10 + 10))) for c in range(3)]
    doc = LinesDoc(60.0, lines, chunks)
    cfg = SimpleNamespace(batch_lines=10, context_lines=2, concurrency=concurrency, max_retries=max_retries)
    w = SimpleNamespace(
        cfg=SimpleNamespace(translate=cfg),
        paths=lambda vid: VideoPaths(tmp_path, vid),
        sched=ChunkScheduler(1),
        models=SimpleNamespace(translator=translator),
        db=SimpleNamespace(update_job=lambda *a, **k: None, get_job=lambda vid: {"title": "A video"}),
        notify=lambda vid: None,
        clock=DubClock(),
    )
    return w, doc


class _FlakyTranslator:
    """Fails the first `fails` attempts at any batch containing line 0."""

    def __init__(self, fails):
        self.fails, self.calls = fails, []

    async def translate(self, req):
        import asyncio

        from yt_jp_redub.pipeline.translate import Retryable

        self.calls.append(req.batch[0].id)
        if req.batch[0].id == 0 and self.fails:
            self.fails -= 1
            raise Retryable("HTTP 500")
        await asyncio.sleep(0.05)
        return {l.id: f"ja {l.id}" for l in req.batch}

    async def glossary(self, title, lines):
        return {}


def test_failed_head_batch_is_retried_first(tmp_path, monkeypatch):
    import asyncio

    from yt_jp_redub.worker import translation

    monkeypatch.setattr(translation, "HEAD_RETRY_CAP", 0.01)
    monkeypatch.setattr(translation.random, "random", lambda: 0.0)
    tr = _FlakyTranslator(fails=2)
    w, doc = _translation_worker(tmp_path, tr, concurrency=1)
    asyncio.run(translation.translate_video(w, "vidvidvidvi", doc))
    assert all(l.ja == f"ja {l.id}" for l in doc.lines)
    # while the first chunk cools down the slot isn't left idle, but once it may go again it
    # goes ahead of the chunks after it
    assert tr.calls == [0, 10, 0, 20, 0]


def test_translation_gives_up_after_max_retries(tmp_path, monkeypatch):
    import asyncio

    from yt_jp_redub.worker import translation

    monkeypatch.setattr(translation, "HEAD_RETRY_CAP", 0.01)
    monkeypatch.setattr(translation, "RETRY_CAP", 0.01)
    w, doc = _translation_worker(tmp_path, _FlakyTranslator(fails=99), max_retries=3)
    with pytest.raises(RuntimeError, match="after 4 attempts"):
        asyncio.run(translation.translate_video(w, "vidvidvidvi", doc))


def test_rate_limiter_waits_only_until_enough_expires():
    from yt_jp_redub.pipeline.translate import RateLimiter

    lim = RateLimiter(rpm=4, tpm=10_000)
    lim.events.extend([(100.0, 6000), (110.0, 3000), (120.0, 500)])
    assert lim._wait(500, now=125.0) == 0  # 10000 tokens, 4 requests: fits
    assert lim._wait(2000, now=125.0) == 35.0  # once the 6000-token request (t=100) leaves the window
    assert lim._wait(9500, now=125.0) == 45.0  # needs the first two gone (t=110 leaves at 170)
    lim.rpm = 3
    assert lim._wait(10, now=125.0) == 35.0  # out of requests, not tokens


def test_rate_limits_follow_the_provider():
    from yt_jp_redub.pipeline.translate import UNLIMITED, api_limits

    google = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert api_limits(google, 0, 0) == (25, 14_000)
    assert api_limits("https://api.x.ai/v1/chat/completions", 0, 0) == (2_000, 10_000_000)
    assert api_limits("http://localhost:11434/v1/chat/completions", 0, 0) == UNLIMITED
    assert api_limits(google, 10, 5000) == (10, 5000)  # config.toml wins


def test_reasoning_effort_falls_back_when_rejected():
    import asyncio
    import json
    from types import SimpleNamespace

    from aiohttp import web

    from yt_jp_redub.pipeline.translate import OpenAITranslator, Request

    bodies = []

    async def chat(req):
        body = await req.json()
        bodies.append(body)
        if "reasoning_effort" in body:
            return web.Response(status=400, text='{"error": "Unknown name \\"reasoning_effort\\""}')
        content = json.dumps({"0": "こんにちは"})
        return web.json_response({"choices": [{"message": {"content": content}}]})

    async def run():
        app = web.Application()
        app.router.add_post("/v1/chat/completions", chat)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        cfg = SimpleNamespace(rpm=100, tpm=100000, temperature=0.4, reasoning_effort="low")
        tr = OpenAITranslator(f"http://127.0.0.1:{port}/v1", "key", "m", cfg)
        try:
            for _ in range(2):
                assert await tr.translate(Request([Line(0, 0, 1, "Hello")], timeout=10)) == {0: "こんにちは"}
        finally:
            await tr.close()
            await runner.cleanup()

    asyncio.run(run())
    assert [("reasoning_effort" in b, "reasoning" in b) for b in bodies] == [
        (True, False),
        (False, True),
        (False, True),  # the accepted shape is remembered
    ]
    assert bodies[1]["reasoning"] == {"effort": "low"}


def test_db_adds_resume_column_to_old_databases(tmp_path):
    import sqlite3

    from yt_jp_redub.db import DB

    path = tmp_path / "db.sqlite"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE jobs (video_id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT, duration REAL,"
        " stage TEXT NOT NULL DEFAULT 'queued', error TEXT, translated INTEGER NOT NULL DEFAULT 0,"
        " total_lines INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,"
        " last_watched_at REAL, size_bytes INTEGER NOT NULL DEFAULT 0)"
    )
    old.execute("INSERT INTO jobs(video_id,url,created_at,updated_at) VALUES('abcdefghijk','u',0,0)")
    old.commit()
    old.close()
    db = DB(path)
    assert db.get_job("abcdefghijk")["resume_at"] is None
    db.update_job("abcdefghijk", resume_at=123.5)
    assert DB(path).get_job("abcdefghijk")["resume_at"] == 123.5


def test_download_filename():
    from urllib.parse import unquote

    from yt_jp_redub.server.app import attachment

    h = attachment('ザ・テスト: A/B? "C"', "abcdefghijk")
    assert h.startswith('attachment; filename="abcdefghijk.mp4"; ')
    assert unquote(h.split("UTF-8''")[1]) == "ザ・テスト A B C [JA dub].mp4"
    assert "abcdefghijk [JA dub].mp4" in unquote(attachment(" ?. ", "abcdefghijk"))
    h = attachment("Title", "abcdefghijk", ".ja.srt")
    assert 'filename="abcdefghijk.ja.srt"' in h and h.endswith("Title%20%5BJA%20dub%5D.ja.srt")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_offline_file_packages_without_reencoding(tmp_path):
    from yt_jp_redub.models import VideoPaths
    from yt_jp_redub.pipeline import export, ff

    paths = VideoPaths(tmp_path, "abcdefghijk")
    paths.root.mkdir()
    lavfi = ["-f", "lavfi", "-i"]
    ff.run_ffmpeg([*lavfi, "testsrc=size=160x90:rate=25:duration=4", "-c:v", "libx264", str(paths.video)])
    ff.run_ffmpeg([*lavfi, "sine=frequency=300:duration=4", "-c:a", "aac", str(paths.audio)])
    doc = LinesDoc(4.0, [Line(0, 0.5, 1.5, "Hello", "こんにちは")], [Chunk(0, 0.0, 2.0), Chunk(1, 2.0, 4.0)])
    for c in doc.chunks:
        wav = tmp_path / f"{c.id}.wav"
        ff.run_ffmpeg(
            [*lavfi, f"sine=frequency=500:duration={c.duration}", "-ar", str(ff.SR), "-ac", "2", str(wav)]
        )
        hls.encode_segment(wav, paths.seg("ja", c.id), c.start)
    paths.subs.mkdir()
    for lang in ("ja", "en"):
        (paths.subs / f"{lang}.srt").write_text(subs.srt(doc, lang), encoding="utf-8")

    out = export.write_file(paths, doc, tmp_path / "out.mp4", "タイトル")
    assert paths.dub_audio.exists()  # made once, then reused by later downloads
    streams = ff.probe(out)["streams"]
    assert [(s["codec_type"], s["codec_name"]) for s in streams] == [
        ("video", "h264"),
        ("audio", "aac"),
        ("audio", "aac"),
        ("subtitle", "mov_text"),
        ("subtitle", "mov_text"),
    ]
    assert [s["tags"]["language"] for s in streams[1:]] == ["jpn", "eng", "jpn", "eng"]
    assert abs(ff.duration(paths.dub_audio) - 4.0) < 0.05  # the joined dub keeps the video's length


def test_qr_renderings():
    from yt_jp_redub import qr

    url = "http://192.168.31.207:8765/"
    rows = qr._modules(url)
    n = len(rows)  # a QR code is 17 + 4 * version modules wide, plus the quiet zone on both sides
    assert (n - 2 * qr.BORDER - 17) % 4 == 0 and all(len(r) == n for r in rows)
    assert not any(rows[0]) and rows[qr.BORDER][qr.BORDER]  # light border, dark finder corner
    lines = qr.terminal(url).splitlines()
    assert len(lines) == (n + 1) // 2 and all(len(l) == n for l in lines)
    assert f'viewBox="0 0 {n} {n}"' in qr.svg(url)


def test_settings_dialog_writes_config_and_env_in_place(tmp_path):
    import shutil
    import tomllib

    import pytest

    from yt_jp_redub.config import DEFAULTS, ROOT, BadSetting, Config, _ns, current_settings, save_settings

    toml, env = tmp_path / "config.toml", tmp_path / ".env"
    shutil.copy(ROOT / "config.example.toml", toml)
    env.write_bytes(b"# my keys\r\nLLM_API_KEY=old\r\nLLM_MODEL=a\r\n")
    cfg = Config(**{k: _ns(v) for k, v in DEFAULTS.items()}, config_file=toml, env_file=env)
    cfg.secrets.llm_api_key, cfg.secrets.llm_model = "old", "a"
    before = toml.read_bytes()

    with pytest.raises(BadSetting) as e:
        save_settings(cfg, {"port": "80", "max_height": "720"})
    assert e.value.name == "port" and toml.read_bytes() == before  # nothing written

    changed = save_settings(
        cfg,
        {"port": "8790", "max_height": "720", "low_vram": "false", "cap_gb": "50", "llm_api_key": ""}
        | {"llm_model": "my model", "cookies_file": ""},
    )
    assert sorted(changed) == ["llm_model", "low_vram", "max_height", "port"]
    text = toml.read_bytes().decode()
    assert "port = 8790               # 8765 is AnkiConnect's" in text  # the comment stays in its column
    assert text.count("\n") == text.count("\r\n")  # and the file's line endings
    data = tomllib.loads(text)
    assert data["server"]["port"] == 8790 and data["server"]["low_vram"] is False
    assert data["download"]["max_height"] == 720
    assert env.read_bytes() == b"# my keys\r\nLLM_API_KEY=old\r\nLLM_MODEL='my model'\r\n"  # the key kept
    assert cfg.server.port == 8790 and cfg.secrets.llm_model == "my model"
    assert current_settings(cfg)["llm_api_key"] is True and current_settings(cfg)["low_vram"] == "false"
    assert save_settings(cfg, {"port": 8790}) == []  # unchanged values aren't written
