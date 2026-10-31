"""HTTP server: library + player pages, JSON API, HLS endpoints with held segments."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import time
from urllib.parse import quote

from aiohttp import web

from .. import qr
from ..config import ROOT, BadSetting, Config, current_settings, needs_api_key, save_settings
from ..models import VID_RE, file_name, is_youtube
from ..net import public_base
from ..pipeline import hls
from ..worker import Worker
from .cards import cards_html

log = logging.getLogger(__name__)
WEB = ROOT / "web"
NO_STORE = {"Cache-Control": "no-store"}


def attachment(title: str, vid: str, ext: str = ".mp4") -> str:
    """Content-Disposition for an offline file: the video's title (UTF-8, for browsers that take
    filename*), with the plain video id as the fallback name. The .mp4 and its .ja.srt/.en.srt
    share the name, so they sit together on the phone."""
    name = file_name(title, 150) or vid
    return f"attachment; filename=\"{vid}{ext}\"; filename*=UTF-8''{quote(name + ' [JA dub]' + ext)}"


def own_host(host: str | None, public_host: str) -> bool:
    """Reached by an IP address, localhost or the configured public_host: not through a website's own
    domain name pointed at this PC (DNS rebinding), which would make that website same-origin."""
    if host in ("localhost", public_host or "localhost"):
        return True
    try:
        ipaddress.ip_address((host or "").strip("[]"))
        return True
    except ValueError:
        return False


def build_app(cfg: Config, worker: Worker, asb=None) -> web.Application:
    @web.middleware
    async def guard(req, handler):
        if not own_host(req.url.host, cfg.server.public_host):
            raise web.HTTPForbidden(text="Open yt-jp-redub by its IP address or localhost")
        # a cross-site <form> can post neither JSON nor htmx's header: so it can't queue, retry or seek
        if (
            req.method == "POST"
            and req.content_type != "application/json"
            and "HX-Request" not in req.headers
        ):
            raise web.HTTPUnsupportedMediaType(text="expected JSON")
        return await handler(req)

    app = web.Application(middlewares=[guard])
    r = app.router

    port = cfg.server.port  # the one it listens on: a new one from the settings waits for a restart

    def base() -> str:
        return public_base(cfg, port)

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

    def cards() -> web.Response:
        """The library's cards, for htmx (which sends HX-Request) to swap in."""
        jobs = [worker.status(j["video_id"]) for j in worker.db.list_jobs()]
        return web.Response(text=cards_html(jobs), content_type="text/html", headers=NO_STORE)

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
            }
        )

    async def api_submit(req):
        data = await json_body(req)
        try:
            vid, created = worker.submit(str(data.get("url", "")))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        go = {"HX-Redirect": f"/watch/{vid}"} if "HX-Request" in req.headers else None
        return web.json_response({"video_id": vid, "created": created, "link": link(vid)}, headers=go)

    async def api_list(req):
        if "HX-Request" in req.headers:
            return cards()
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
        return cards() if "HX-Request" in req.headers else web.json_response({"ok": True})

    async def api_delete(req):
        vid = req.match_info["vid"]
        job_or_404(vid)
        try:
            worker.delete(vid)
        except RuntimeError as e:
            return web.json_response({"error": str(e)}, status=409)
        return cards() if "HX-Request" in req.headers else web.json_response({"ok": True})

    async def api_asbplayer(req):
        vid = req.match_info["vid"]
        job_or_404(vid)
        if not asb:
            return web.json_response({"clients": 0})
        n = await asb.send_subtitles(vid, force=True)
        return web.json_response({"clients": n})

    def on_this_pc(req) -> bool:
        """A browser on this PC (from loopback, or from the LAN address it was reached at), on this
        server's own page. The Host check keeps out other websites reaching it through a domain name
        that points at this PC."""
        here = {"127.0.0.1", "::1", req.transport and req.transport.get_extra_info("sockname")[0]}
        own = req.headers.get("Origin", f"http://{req.host}") == f"http://{req.host}"
        return req.remote in here and req.url.host in here | {"localhost"} and own

    async def api_settings(req):
        return web.json_response(
            {**current_settings(cfg), "editable": on_this_pc(req), "first_run": needs_api_key(cfg)},
            headers=NO_STORE,
        )

    async def api_save_settings(req):
        if not on_this_pc(req):
            raise web.HTTPForbidden(text="Settings can be changed only on the PC that runs yt-jp-redub")
        if req.content_type != "application/json":  # a cross-site form can't send JSON without asking
            raise web.HTTPUnsupportedMediaType(text="expected JSON")
        data = await json_body(req)
        try:
            changed = save_settings(cfg, dict(data))
        except BadSetting as e:
            return web.json_response({"error": str(e), "field": e.name}, status=400)
        except OSError as e:
            return web.json_response({"error": f"The settings couldn't be saved: {e}"}, status=500)
        worker.models.settings_changed(changed)
        if changed:
            log.info("Settings changed: %s", ", ".join(changed))
        return web.json_response({"changed": changed, "restart": "port" in changed})

    async def qr_code(_):
        return web.Response(text=qr.svg(base() + "/"), content_type="image/svg+xml", headers=NO_STORE)

    r.add_get("/qr.svg", qr_code)
    r.add_get("/api/info", api_info)
    r.add_get("/api/settings", api_settings)
    r.add_post("/api/settings", api_save_settings)
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
        if not worker.ready(vid):
            raise web.HTTPServiceUnavailable(text="not ready", headers={"Retry-After": "3"})
        audio = req.query.get("audio")
        langs = (audio,) if audio in ("ja", "en") else ("ja", "en")
        text = await asyncio.to_thread(hls.master_playlist, paths, langs)
        return web.Response(text=text, content_type="application/vnd.apple.mpegurl", headers=NO_STORE)

    async def video_playlist(req):
        _, paths = media_paths(req)
        p = paths.video_playlist
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
        if job["stage"] != "done" or not worker.ready(vid):
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
