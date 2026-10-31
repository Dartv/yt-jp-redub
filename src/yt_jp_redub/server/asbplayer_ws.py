"""WebSocket server for asbplayer's WebSocket client (PC).

asbplayer connects to ws://127.0.0.1:8766/ws (enabled once in its misc settings).
When a video's full subtitles are ready and a player page is open, we send
`load-subtitles` with the ja and en files once. asbplayer can't append to loaded
subtitles, which is why the whole subtitle file has to exist first.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid

from aiohttp import WSMsgType, web

from ..models import file_name

log = logging.getLogger(__name__)
WATCH_TTL = 30  # seconds since the last playhead report for a page to count as open


class AsbplayerBridge:
    def __init__(self, worker):
        self.worker = worker
        self.clients: dict[web.WebSocketResponse, set[str]] = {}  # client -> videos sent to it
        self.watching_at: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/ws", self.handle)
        app.router.add_get("/", self.handle)
        return app

    async def handle(self, req: web.Request) -> web.WebSocketResponse:
        # asbplayer connects from its extension; any web page open in the browser could too, and read
        # the subtitles and titles of what is being watched
        origin = req.headers.get("Origin", "")
        if origin.startswith(("http://", "https://")) and origin != "https://app.asbplayer.dev":
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(req)
        self.clients[ws] = set()
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
            self.clients.pop(ws, None)
        return ws

    def watching(self, vid: str) -> None:
        """Called on every playhead report from a page that wants asbplayer subtitles."""
        first = vid not in self.watching_at or time.time() - self.watching_at[vid] > WATCH_TTL
        self.watching_at[vid] = time.time()
        if first:
            self._spawn(self._maybe_send(vid))

    def subs_ready(self, vid: str) -> None:
        self._spawn(self._maybe_send(vid))

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _maybe_send(self, vid: str) -> None:
        if time.time() - self.watching_at.get(vid, 0) < WATCH_TTL:
            st = self.worker.status(vid)
            if st and st["subs_complete"]:
                await self.send_subtitles(vid)

    async def send_subtitles(self, vid: str, force: bool = False) -> int:
        paths = self.worker.paths(vid)
        files = []
        title = (self.worker.db.get_job(vid) or {}).get("title") or vid
        safe = file_name(title, 80) or vid
        for lang in ("ja", "en"):
            p = paths.subs / f"{lang}.srt"
            if p.exists():
                files.append(
                    {"name": f"{safe}.{lang}.srt", "base64": base64.b64encode(p.read_bytes()).decode()}
                )
        if not files:
            return 0
        n = 0
        for ws, sent in list(self.clients.items()):
            if vid in sent and not force:
                continue
            try:
                await ws.send_json(
                    {"command": "load-subtitles", "messageId": str(uuid.uuid4()), "body": {"files": files}}
                )
                sent.add(vid)
                n += 1
            except ConnectionError:
                self.clients.pop(ws, None)
        if n:
            log.info("Sent %s subtitles to %d asbplayer client(s)", vid, n)
        return n
