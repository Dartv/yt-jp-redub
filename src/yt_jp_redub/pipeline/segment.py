"""Words -> subtitle lines -> dub chunks (~60 s, split at silences, tiling the whole timeline)."""

from __future__ import annotations

from ..models import Chunk, Line, LinesDoc, Word

SENT_END = (".", "?", "!", "…")
CLAUSE_END = (",", ";", ":", "—", "-")
ABBREV = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "jr.", "sr.", "e.g.", "i.e.", "etc.", "prof."}


def words_to_lines(
    words: list[Word], max_chars: int = 90, max_sec: float = 7.0, pause_split: float = 0.5
) -> list[Line]:
    lines: list[Line] = []
    cur: list[Word] = []

    def flush():
        if cur:
            lines.append(
                Line(id=len(lines), start=cur[0].start, end=cur[-1].end, en=" ".join(w.text for w in cur))
            )
            cur.clear()

    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None:
            break
        text_len = sum(len(x.text) + 1 for x in cur)
        gap = nxt.start - w.end
        if gap >= pause_split:
            flush()
        elif w.text.endswith(SENT_END) and w.text.lower() not in ABBREV:
            # every sentence gets its own line: a sentence end is often a speaker change,
            # and each line is voiced with its own original audio as reference
            flush()
        elif text_len + len(nxt.text) > max_chars or nxt.end - cur[0].start > max_sec:
            # split at the last clause boundary in the line if it leaves a reasonable tail
            cut = next(
                (
                    k
                    for k in range(len(cur) - 1, 0, -1)
                    if cur[k - 1].text.endswith(CLAUSE_END) and k >= len(cur) * 0.3
                ),
                None,
            )
            if cut is None:
                flush()
            else:
                tail = cur[cut:]
                del cur[cut:]
                flush()
                cur.extend(tail)
    flush()
    return lines


def lines_to_chunks(
    lines: list[Line], duration: float, chunk_sec: float = 60.0, min_gap: float = 0.3
) -> list[Chunk]:
    """Partitions [0, duration] into chunks whose boundaries fall in gaps between lines."""
    chunks: list[Chunk] = []
    start = 0.0
    cur: list[Line] = []
    i = 0
    while i < len(lines):
        cur.append(lines[i])
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if nxt is not None and nxt.start - start >= chunk_sec:
            gap = nxt.start - lines[i].end
            overdue = lines[i].end - start >= chunk_sec * 1.5
            if gap >= min_gap or overdue:
                # when overdue, cut at the largest gap in the last part of the chunk
                if gap < min_gap:
                    best = max(
                        range(len(cur) // 2, len(cur)), key=lambda k: lines[cur[k].id + 1].start - cur[k].end
                    )
                    tail = cur[best + 1 :]
                    cur = cur[: best + 1]
                    i -= len(tail)
                    nxt = lines[cur[-1].id + 1]
                cut = round((cur[-1].end + nxt.start) / 2, 3)
                chunks.append(Chunk(len(chunks), start, cut, [l.id for l in cur]))
                start, cur = cut, []
        i += 1
    chunks.append(Chunk(len(chunks), start, round(duration, 3), [l.id for l in cur]))
    if len(chunks) > 1 and chunks[-1].duration < chunk_sec * 0.25:
        last = chunks.pop()
        chunks[-1].end = last.end
        chunks[-1].line_ids += last.line_ids
    for c in chunks:
        for lid in c.line_ids:
            lines[lid].chunk = c.id
    return chunks


def build(words: list[Word], duration: float, cfg) -> LinesDoc:
    lines = words_to_lines(words, cfg.max_line_chars, cfg.max_line_sec, cfg.pause_split_sec)
    for l in lines:
        l.end = min(l.end, duration)
    return LinesDoc(duration, lines, lines_to_chunks(lines, duration, cfg.chunk_sec))
