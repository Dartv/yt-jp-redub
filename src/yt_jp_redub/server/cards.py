"""The library's video cards, as HTML for htmx to swap in."""

from __future__ import annotations

from html import escape

DL_ICON = (
    '<svg viewBox="0 0 24 24"><path d="M11 4h2v8.2l3.3-3.3 1.4 1.4L12 16l-5.7-5.7 1.4-1.4 3.3 3.3z'
    'M5 18h14v2H5z"/></svg>'
)
EMPTY = '<div class="empty">Paste a YouTube or Twitch link to dub your first video.</div>'


def clock(s: float | None) -> str:
    """Seconds as h:mm:ss or m:ss."""
    s = max(0, int(s or 0))
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m}:{x:02d}"


def size(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


def state_text(j: dict) -> str:
    stage, pct = j["stage"], j.get("percent") or 0
    if stage == "done":
        return f"Ready · {size(j['download_size'])}" if j.get("download_size") else "Ready"
    text = j["label"]
    if stage == "queued" and j.get("position"):
        text += f" · #{j['position'] + 1}"
    if stage not in ("queued", "error"):
        text += f" · {pct}%"
    if j.get("error_hint"):
        text += f" · {j['error_hint'][0]}"
    if j.get("chunks_done") and stage != "error":
        text += " · watchable"
    return text


def card(j: dict) -> str:
    vid, stage, pct = escape(j["video_id"]), escape(j["stage"]), j.get("percent") or 0
    watch, title, error = f"/watch/{vid}", escape(j.get("title") or ""), escape(j.get("error") or "")
    dur = f'<span class="dur">{clock(j["duration"])}</span>' if j.get("duration") else ""
    seen = ""  # how far it has been watched, as a red line along the thumbnail's bottom (as on YouTube)
    if stage == "done" and j.get("resume_at") and j.get("duration"):
        w = min(100, max(2, 100 * j["resume_at"] / j["duration"]))
        seen = f'<span class="watched"><i style="width:{w}%"></i></span>'
    buttons = ""
    if stage == "error":
        buttons += (
            f'<button class="retry" id="retry-{vid}" hx-post="/api/jobs/{vid}/retry" title="Retry"'
            ' aria-label="Retry">↻</button>'
        )
    if stage == "done" and j.get("ready"):  # one tap saves the .mp4 and its subtitles (see common.js)
        tip = f" ({size(j['download_size'])})" if j.get("download_size") else ""
        buttons += (
            f'<button class="dl" id="dl-{vid}" title="Download the video and its subtitles{tip}"'
            f' aria-label="Download">{DL_ICON}</button>'
        )
    buttons += (
        f'<button class="del" id="del-{vid}" hx-delete="/api/jobs/{vid}"'
        ' hx-confirm="Delete this video and its dub?" title="Delete" aria-label="Delete">✕</button>'
    )
    return f"""<article class="card {stage}" data-id="{vid}">
    <a class="thumb" id="thumb-{vid}" href="{watch}" style="background-image:url('/media/{vid}/thumb.jpg')">
      {dur}<span class="bar"><i style="width:{pct}%"></i></span>{seen}</a>
    <a class="title" id="title-{vid}" href="{watch}" title="{title}">{title or vid}</a>
    <div class="meta"><span class="state" title="{error}">{escape(state_text(j))}</span>
      {buttons}</div>
  </article>"""


def cards_html(jobs: list[dict]) -> str:
    return "".join(map(card, jobs)) or EMPTY
