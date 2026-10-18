"""WebVTT / SRT writers."""

from __future__ import annotations

from pathlib import Path

from ..models import LinesDoc


def _ts(t: float, sep: str = ".") -> str:
    ms = round(max(0.0, t) * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def cues(doc: LinesDoc, lang: str) -> list[tuple[float, float, str]]:
    out = []
    for l in doc.lines:
        text = l.ja if lang == "ja" else l.en
        if text:
            # keep cues readable: at least 0.8 s on screen, never past the next line's start
            out.append((l.start, max(l.end, l.start + 0.8), text.strip()))
    for i in range(len(out) - 1):
        if out[i][1] > out[i + 1][0]:
            out[i] = (out[i][0], out[i + 1][0], out[i][2])
    return out


def vtt(doc: LinesDoc, lang: str) -> str:
    parts = ["WEBVTT", ""]
    for s, e, text in cues(doc, lang):
        parts += [f"{_ts(s)} --> {_ts(e)}", text.replace("-->", "→"), ""]
    return "\n".join(parts)


def srt(doc: LinesDoc, lang: str) -> str:
    parts = []
    for i, (s, e, text) in enumerate(cues(doc, lang), 1):
        parts += [str(i), f"{_ts(s, ',')} --> {_ts(e, ',')}", text, ""]
    return "\n".join(parts)


def write_all(doc: LinesDoc, subs_dir: Path) -> None:
    subs_dir.mkdir(parents=True, exist_ok=True)
    for lang in ("en", "ja"):
        for ext, fn in (("vtt", vtt), ("srt", srt)):
            p = subs_dir / f"{lang}.{ext}"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(fn(doc, lang), encoding="utf-8")
            tmp.replace(p)
