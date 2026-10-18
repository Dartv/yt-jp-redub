"""Optional Telegram adapter: long polling, one status message per job edited in place.

Enabled only when TELEGRAM_BOT_TOKEN is set; answers only TELEGRAM_ALLOWED_USER_ID.
Any message containing YouTube URL(s) is queued. There are no commands.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time

import aiohttp

from ..models import find_youtube_urls

log = logging.getLogger(__name__)
STAGE_TEXT = {
    "queued": "⏳ queued", "downloading": "⬇️ downloading", "transcribing": "📝 transcribing",
    "translating": "🌐 translating", "dubbing": "🎙️ dubbing", "done": "✅ dub complete", "error": "❌ failed",
}


class TelegramBot:
    def __init__(self, token: str, allowed_user: int | None, worker, link_fn):
        self.api = f"https://api.telegram.org/bot{token}/"
        self.allowed, self.worker, self.link = allowed_user, worker, link_fn
        self.session: aiohttp.ClientSession | None = None
        self._dirty: set[str] = set()
        self._last_edit: dict[str, float] = {}
        worker.listeners.append(self._on_update)

    async def call(self, method: str, **params) -> dict:
        async with self.session.post(self.api + method, json=params) as r:
            data = await r.json()
        if not data.get("ok"):
            raise RuntimeError(f"{method}: {data.get('description')}")
        return data["result"]

    def _on_update(self, vid: str) -> None:
        if self.worker.db.notifies(vid):
            self._dirty.add(vid)

    def _caption(self, vid: str) -> str:
        st = self.worker.status(vid)
        title = html.escape(st["title"] or vid)
        line = STAGE_TEXT.get(st["stage"], st["stage"])
        if st["stage"] == "queued" and st["position"]:
            line += f" (#{st['position'] + 1} in line)"
        if st["stage"] in ("translating", "dubbing"):
            line += f" — {st['percent']}%"
            if st["chunks_total"]:
                line += f" ({st['chunks_done']}/{st['chunks_total']} parts dubbed)"
        if st["stage"] == "error" and st["error"]:
            line += f"\n<code>{html.escape(st['error'][:300])}</code>"
        return f"<b>{title}</b>\n{line}\n\n▶️ {html.escape(self.link(vid))}"

    async def _handle(self, msg: dict) -> None:
        user = (msg.get("from") or {}).get("id")
        if self.allowed is None or user != self.allowed:
            log.info("Ignoring Telegram message from user %s", user)
            return
        text = (msg.get("text") or "") + " " + (msg.get("caption") or "")
        for ent in msg.get("entities", []) + msg.get("caption_entities", []):
            if ent.get("url"):
                text += " " + ent["url"]
        urls = find_youtube_urls(text)
        if not urls:
            await self.call("sendMessage", chat_id=msg["chat"]["id"], text="Send me a YouTube link to dub.")
            return
        for url in urls:
            vid, _ = self.worker.submit(url)
            caption = self._caption(vid)
            try:
                sent = await self.call("sendPhoto", chat_id=msg["chat"]["id"], parse_mode="HTML", caption=caption,
                                       photo=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
            except RuntimeError:
                sent = await self.call("sendMessage", chat_id=msg["chat"]["id"], parse_mode="HTML", text=caption)
            self.worker.db.add_notify(vid, msg["chat"]["id"], sent["message_id"])
            self.worker.db.set_notify_text(vid, msg["chat"]["id"], sent["message_id"], caption)

    async def _edit_loop(self) -> None:
        while True:
            await asyncio.sleep(2)
            for vid in list(self._dirty):
                st = self.worker.status(vid)
                final = st is None or st["stage"] in ("done", "error")
                # Telegram rate-limits edits; progress updates at most every 10 s
                if not final and time.time() - self._last_edit.get(vid, 0) < 10:
                    continue
                self._dirty.discard(vid)
                if st is None:
                    continue
                self._last_edit[vid] = time.time()
                caption = self._caption(vid)
                for n in self.worker.db.notifies(vid):
                    if n["last_text"] == caption:
                        continue
                    try:
                        try:
                            await self.call("editMessageCaption", chat_id=n["chat_id"], message_id=n["message_id"],
                                            caption=caption, parse_mode="HTML")
                        except RuntimeError as e:
                            if "no caption" not in str(e).lower() and "there is no" not in str(e).lower():
                                raise
                            await self.call("editMessageText", chat_id=n["chat_id"], message_id=n["message_id"],
                                            text=caption, parse_mode="HTML")
                        self.worker.db.set_notify_text(vid, n["chat_id"], n["message_id"], caption)
                    except Exception as e:
                        log.warning("Telegram edit failed: %s", e)

    async def run(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=70))
        me = await self.call("getMe")
        log.info("Telegram bot @%s polling", me.get("username"))
        if self.allowed is None:
            log.warning("TELEGRAM_ALLOWED_USER_ID is not set: the bot will ignore everyone")
        editor = asyncio.create_task(self._edit_loop())
        offset = 0
        try:
            while True:
                try:
                    updates = await self.call("getUpdates", offset=offset, timeout=50,
                                              allowed_updates=["message"])
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
                    log.warning("Telegram polling error: %s", e)
                    await asyncio.sleep(5)
                    continue
                for u in updates:
                    offset = u["update_id"] + 1
                    if "message" in u:
                        try:
                            await self._handle(u["message"])
                        except Exception:
                            log.exception("Telegram message handling failed")
        finally:
            editor.cancel()
            await self.session.close()
