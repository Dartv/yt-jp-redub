"""WebSocket server for asbplayer's WebSocket client (PC).

asbplayer connects to ws://127.0.0.1:8766/ws (enabled once in its misc settings).
When a video's full subtitles are ready and a player page is open, we send
`load-subtitles` with the ja and en files once. asbplayer can't append to loaded
subtitles, which is why the whole subtitle file has to exist first.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid

from aiohttp import WSMsgType, web

log = logging.getLogger(__name__)
WATCH_TTL = 30  # seconds since the last playhead report for a page to count as open


class AsbplayerBridge:
    def __init__(self, worker):
        self.worker = worker
        self.clients: set[web.WebSocketResponse] = set()
        self.watching_at: dict[str, float] = {}
        self.sent: set[tuple[int, str]] = set()  # (client id, video_id)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/ws", self.handle)
        app.router.add_get("/", self.handle)
        return app

    async def handle(self, req: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(req)
        self.clients.add(ws)
        log.info("asbplayer connected (%d client(s))", len(self.clients))
        for vid in list(self.watching_at):
            await self._maybe_send(vid)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except ValueError:
                        continue
                    if data.get("command") == "response":
                        log.debug("asbplayer response %s", data.get("messageId"))
                    elif data.get("command") == "ping":
                        await ws.send_json({"command": "pong"})
        finally:
            self.clients.discard(ws)
        return ws

    def watching(self, vid: str) -> None:
        """Called on every playhead report from a page that wants asbplayer subtitles."""
        first = vid not in self.watching_at or time.time() - self.watching_at[vid] > WATCH_TTL
        self.watching_at[vid] = time.time()
        if first:
            import asyncio

            asyncio.get_running_loop().create_task(self._maybe_send(vid))

    def subs_ready(self, vid: str) -> None:
        import asyncio

        asyncio.get_running_loop().create_task(self._maybe_send(vid))

    async def _maybe_send(self, vid: str) -> None:
        if time.time() - self.watching_at.get(vid, 0) < WATCH_TTL:
            st = self.worker.status(vid)
            if st and st["subs_complete"]:
                await self.send_subtitles(vid)

    async def send_subtitles(self, vid: str, force: bool = False) -> int:
        paths = self.worker.paths(vid)
        files = []
        title = (self.worker.db.get_job(vid) or {}).get("title") or vid
        safe = "".join(ch for ch in title if ch not in '\\/:*?"<>|')[:80]
        for lang in ("ja", "en"):
            p = paths.subs / f"{lang}.srt"
            if p.exists():
                files.append(
                    {"name": f"{safe}.{lang}.srt", "base64": base64.b64encode(p.read_bytes()).decode()}
                )
        if not files:
            return 0
        n = 0
        for ws in list(self.clients):
            key = (id(ws), vid)
            if key in self.sent and not force:
                continue
            try:
                await ws.send_json(
                    {"command": "load-subtitles", "messageId": str(uuid.uuid4()), "body": {"files": files}}
                )
                self.sent.add(key)
                n += 1
            except ConnectionError:
                self.clients.discard(ws)
        if n:
            log.info("Sent %s subtitles to %d asbplayer client(s)", vid, n)
        return n
