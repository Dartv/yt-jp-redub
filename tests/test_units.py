import itertools
import struct
import time

from yt_jp_redub.models import Line, LinesDoc, Word, youtube_id
from yt_jp_redub.pipeline import hls, subs
from yt_jp_redub.pipeline.asr import tokens_to_words
from yt_jp_redub.pipeline.segment import lines_to_chunks, words_to_lines
from yt_jp_redub.worker.scheduler import ChunkScheduler


def test_youtube_ids():
    assert youtube_id("https://www.youtube.com/watch?v=TpQlyUjp3vM&t=10") == "TpQlyUjp3vM"
    assert youtube_id("https://youtu.be/TpQlyUjp3vM?si=x") == "TpQlyUjp3vM"
    assert youtube_id("https://www.youtube.com/live/abcdefghijk") == "abcdefghijk"
    assert youtube_id("https://m.youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert youtube_id("https://example.com/") is None


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


def test_chunks_no_speech():
    chunks = lines_to_chunks([], 30.0)
    assert len(chunks) == 1 and chunks[0].end == 30.0


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
    from yt_jp_redub.models import Chunk

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
    import pytest

    from yt_jp_redub.pipeline.translate import normalize_endpoint

    slip = "LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert normalize_endpoint(slip, "m") == ("https://generativelanguage.googleapis.com/v1beta/openai", "m")
    with pytest.raises(RuntimeError):
        normalize_endpoint("not a url", "m")
