"""HTTP server: library + player pages, JSON API, HLS endpoints with held segments."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from urllib.parse import quote

from aiohttp import web

from .. import qr
from ..config import ROOT, Config
from ..models import VID_RE, is_youtube
from ..net import public_base
from ..pipeline import hls
from ..worker import Worker

log = logging.getLogger(__name__)
WEB = ROOT / "web"
NO_STORE = {"Cache-Control": "no-store"}


def attachment(title: str, vid: str, ext: str = ".mp4") -> str:
    """Content-Disposition for an offline file: the video's title (UTF-8, for browsers that take
    filename*), with the plain video id as the fallback name. The .mp4 and its .ja.srt/.en.srt
    share the name, so they sit together on the phone."""
    name = " ".join(re.sub(r'[\x00-\x1f\\/:*?"<>|]', " ", title).split()).strip(".")[:150] or vid
    return f"attachment; filename=\"{vid}{ext}\"; filename*=UTF-8''{quote(name + ' [JA dub]' + ext)}"


def build_app(cfg: Config, worker: Worker, asb=None) -> web.Application:
    app = web.Application()
    r = app.router

    def base() -> str:
        return public_base(cfg)

    def link(vid: str) -> str:
        return f"{base()}/watch/{vid}"

    def job_or_404(vid: str) -> dict:
        job = worker.db.get_job(vid)
        if job is None:
            raise web.HTTPNotFound(text="unknown video")
        return job

    async def json_body(req) -> dict:
        try:
            data = await req.json() if req.content_type == "application/json" else await req.post()
        except ValueError:
            raise web.HTTPBadRequest(text="invalid JSON") from None
        if not hasattr(data, "get"):
            raise web.HTTPBadRequest(text="expected an object")
        return data

    # ------------------------------------------------------------- pages
    async def index(_):
        return web.FileResponse(WEB / "index.html", headers=NO_STORE)

    async def watch(req):
        job_or_404(req.match_info["vid"])
        return web.FileResponse(WEB / "player.html", headers=NO_STORE)

    async def add(req):
        url = req.query.get("url", "")
        try:
            vid, _ = worker.submit(url)
        except ValueError:
            raise web.HTTPBadRequest(text=f"Not a YouTube video or Twitch VOD/clip URL: {url}") from None
        raise web.HTTPFound(f"/watch/{vid}")

    r.add_get("/", index)
    r.add_get(f"/watch/{{vid:{VID_RE}}}", watch)
    r.add_get("/add", add)
    r.add_static("/static/", WEB, append_version=False)

    # ------------------------------------------------------------- API
    async def api_info(_):
        b = base()
        return web.json_response(
            {
                "base_url": b,
                "ws_port": cfg.server.ws_port,
                "bookmarklet": f"javascript:location.href='{b}/add?url='+encodeURIComponent(location.href)",
                "head_start_sec": cfg.stream.head_start_sec,
            }
        )

    async def api_submit(req):
        data = await json_body(req)
        try:
            vid, created = worker.submit(str(data.get("url", "")))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"video_id": vid, "created": created, "link": link(vid)})

    async def api_list(_):
        out = []
        for j in worker.db.list_jobs():
            st = worker.status(j["video_id"])
            st.pop("chunks", None)
            out.append(st)
        return web.json_response({"jobs": out, "current": worker.current})

    async def api_status(req):
        st = worker.status(req.match_info["vid"])
        if st is None:
            raise web.HTTPNotFound(text="unknown video")
        return web.json_response(st, headers=NO_STORE)

    async def api_lines(req):
        vid = req.match_info["vid"]
        job_or_404(vid)
        doc = worker.doc(vid)
        lines = (
            [{"id": l.id, "start": l.start, "end": l.end, "en": l.en, "ja": l.ja} for l in doc.lines]
            if doc
            else []
        )
        return web.json_response({"lines": lines}, headers=NO_STORE)

    async def api_playhead(req):
        vid = req.match_info["vid"]
        job_or_404(vid)
        data = await json_body(req)
        try:
            t = float(data.get("t", 0))
        except (TypeError, ValueError):
            t = math.nan
        if not math.isfinite(t):
            raise web.HTTPBadRequest(text="t must be a number of seconds")
        worker.report_playhead(vid, t, save=bool(data.get("save", True)))
        if asb and data.get("asbplayer"):
            asb.watching(vid)
        return web.json_response({"ok": True})

    async def api_retry(req):
        vid = req.match_info["vid"]
        job = job_or_404(vid)
        worker.submit(job["url"])
        return web.json_response({"ok": True})

    async def api_delete(req):
        vid = req.match_info["vid"]
        job_or_404(vid)
        try:
            worker.delete(vid)
        except RuntimeError as e:
            return web.json_response({"error": str(e)}, status=409)
        return web.json_response({"ok": True})

    async def api_asbplayer(req):
        vid = req.match_info["vid"]
        job_or_404(vid)
        if not asb:
            return web.json_response({"clients": 0})
        n = await asb.send_subtitles(vid, force=True)
        return web.json_response({"clients": n})

    async def qr_code(_):
        return web.Response(text=qr.svg(base() + "/"), content_type="image/svg+xml", headers=NO_STORE)

    r.add_get("/qr.svg", qr_code)
    r.add_get("/api/info", api_info)
    r.add_post("/api/jobs", api_submit)
    r.add_get("/api/jobs", api_list)
    r.add_get(f"/api/jobs/{{vid:{VID_RE}}}", api_status)
    r.add_get(f"/api/jobs/{{vid:{VID_RE}}}/lines", api_lines)
    r.add_post(f"/api/jobs/{{vid:{VID_RE}}}/playhead", api_playhead)
    r.add_post(f"/api/jobs/{{vid:{VID_RE}}}/retry", api_retry)
    r.add_post(f"/api/jobs/{{vid:{VID_RE}}}/asbplayer", api_asbplayer)
    r.add_delete(f"/api/jobs/{{vid:{VID_RE}}}", api_delete)

    # ------------------------------------------------------------- media
    def media_paths(req):
        vid = req.match_info["vid"]
        if not worker.db.get_job(vid):
            raise web.HTTPNotFound()
        return vid, worker.paths(vid)

    async def master(req):
        vid, paths = media_paths(req)
        if not (paths.hls / "video.m3u8").exists() or worker.doc(vid) is None:
            raise web.HTTPServiceUnavailable(text="not ready", headers={"Retry-After": "3"})
        audio = req.query.get("audio")
        langs = (audio,) if audio in ("ja", "en") else ("ja", "en")
        text = await asyncio.to_thread(hls.master_playlist, paths, langs)
        return web.Response(text=text, content_type="application/vnd.apple.mpegurl", headers=NO_STORE)

    async def video_playlist(req):
        _, paths = media_paths(req)
        p = paths.hls / "video.m3u8"
        if not p.exists():
            raise web.HTTPServiceUnavailable(headers={"Retry-After": "3"})
        return web.FileResponse(p, headers={"Content-Type": "application/vnd.apple.mpegurl", **NO_STORE})

    async def video_file(req):
        _, paths = media_paths(req)
        name = req.match_info["name"]
        p = paths.hls / "video" / name
        if not p.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Cache-Control": "max-age=86400"})

    async def audio_playlist(req):
        vid, _ = media_paths(req)
        doc = worker.doc(vid)
        if doc is None:
            raise web.HTTPServiceUnavailable(headers={"Retry-After": "3"})
        return web.Response(
            text=hls.audio_playlist(doc, req.match_info["lang"]),
            content_type="application/vnd.apple.mpegurl",
            headers=NO_STORE,
        )

    async def audio_segment(req):
        vid, paths = media_paths(req)
        lang, n = req.match_info["lang"], int(req.match_info["n"])
        doc = worker.doc(vid)
        if doc is None or n >= len(doc.chunks):
            raise web.HTTPNotFound()
        p = paths.seg(lang, n)
        if not p.exists() and lang == "ja":
            # hold the request: it doubles as a priority hint for the worker
            worker.request_segment(vid, n)
            deadline = time.monotonic() + cfg.stream.hold_sec
            while not p.exists() and time.monotonic() < deadline:
                await worker.wait_change(min(2.0, max(0.1, deadline - time.monotonic())))
        if not p.exists():
            raise web.HTTPServiceUnavailable(text="not dubbed yet", headers={"Retry-After": "2", **NO_STORE})
        return web.FileResponse(p, headers={"Content-Type": "audio/aac", "Cache-Control": "no-cache"})

    async def subtitle(req):
        _, paths = media_paths(req)
        lang, ext = req.match_info["lang"], req.match_info["ext"]
        p = paths.subs / f"{lang}.{ext}"
        if not p.exists():
            raise web.HTTPNotFound()
        ctype = "text/vtt" if ext == "vtt" else "application/x-subrip"
        return web.Response(
            body=p.read_bytes(), headers={"Content-Type": f"{ctype}; charset=utf-8", **NO_STORE}
        )

    async def thumb(req):
        vid, paths = media_paths(req)
        if paths.thumb.exists():
            return web.FileResponse(paths.thumb)
        if not is_youtube(vid):
            raise web.HTTPNotFound()
        raise web.HTTPFound(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg")

    async def download(req):
        """The .mp4 for offline viewing, packaged now from the finished streams."""
        vid = req.match_info["vid"]
        job = job_or_404(vid)
        if job["stage"] != "done" or not worker.status(vid)["ready"]:
            raise web.HTTPConflict(text="Only a fully dubbed video can be downloaded")
        try:
            path = await worker.offline_file(vid)
        except Exception:
            log.exception("Packaging %s for download failed", vid)
            raise web.HTTPInternalServerError(text="Couldn't build the file; see the server log") from None
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "video/mp4",
                "Content-Disposition": attachment(job["title"] or vid, vid),
            },
        )

    async def download_subs(req):
        vid, paths = media_paths(req)
        lang = req.match_info["lang"]
        p = paths.subs / f"{lang}.srt"
        if not p.exists():
            raise web.HTTPNotFound()
        title = worker.db.get_job(vid)["title"] or vid
        return web.Response(
            body=p.read_bytes(),
            headers={
                "Content-Type": "application/x-subrip; charset=utf-8",
                "Content-Disposition": attachment(title, vid, f".{lang}.srt"),
                **NO_STORE,
            },
        )

    m = f"/media/{{vid:{VID_RE}}}"
    r.add_get(f"{m}/master.m3u8", master)
    r.add_get(f"{m}/video.m3u8", video_playlist)
    r.add_get(f"{m}/video/{{name:[A-Za-z0-9_.-]+}}", video_file)
    r.add_get(f"{m}/audio_{{lang:ja|en}}.m3u8", audio_playlist)
    r.add_get(f"{m}/{{lang:ja|en}}/{{n:\\d+}}.aac", audio_segment)
    r.add_get(f"{m}/subs/{{lang:ja|en}}.{{ext:vtt|srt}}", subtitle)
    r.add_get(f"{m}/thumb.jpg", thumb)
    r.add_get(f"{m}/download.mp4", download)
    r.add_get(f"{m}/download.{{lang:ja|en}}.srt", download_subs)

    @web.middleware
    async def headers(req, handler):
        resp = await handler(req)
        # media for players on other origins (asbplayer); the API stays private to this page
        if req.path.startswith("/media/"):
            resp.headers.setdefault("Access-Control-Allow-Origin", "*")
        if req.path.startswith("/static/"):  # revalidate, so an upgrade's JS/CSS is picked up at once
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp

    app.middlewares.append(headers)
    return app
