"""English -> natural spoken Japanese through any OpenAI-compatible chat API.

Batches of ~40 lines with preceding lines as context, JSON in and out keyed by
line ID, and a check that every ID comes back. Gemma on Google AI Studio
rejects system prompts, so by default the instructions go into the user message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections import deque

import aiohttp

from ..models import Line

log = logging.getLogger(__name__)

INSTRUCTIONS = """You translate English video dialogue into natural spoken Japanese for a Japanese dub.
The audience is Japanese learners who will listen to the dub, so the Japanese must sound like real people talking.

Rules:
- Match each speaker's register: casual stays casual (タメ口, contractions, fillers), polite stays polite, rude stays rude. Don't make everything です/ます.
- Keep the emotion and personality: jokes, sarcasm, anger, excitement.
- The dub has to fit the original timing, so keep each line about as short as the English. Prefer concise natural phrasing over literal completeness.
- Translate each line on its own (lines are spoken separately), but use the surrounding lines to keep pronouns, names and tone consistent.
- Keep names in katakana; well-known brand or game names may stay in Latin letters.
- Fillers like "uh", "um" become natural Japanese fillers (えっと, あの) or are dropped.
- Write numbers the way they'd be spoken naturally; avoid symbols the voice can't read.
- Output ONLY a JSON object mapping every line id (as a string) to its Japanese translation. No comments, no markdown.
"""


class RateLimiter:
    def __init__(self, rpm: int, tpm: int):
        self.rpm, self.tpm = max(1, rpm), max(1000, tpm)
        self.events: deque[tuple[float, int]] = deque()
        self.lock = asyncio.Lock()

    async def acquire(self, tokens: int) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                while self.events and now - self.events[0][0] > 60:
                    self.events.popleft()
                used = sum(t for _, t in self.events)
                if len(self.events) < self.rpm and used + tokens <= self.tpm or not self.events:
                    self.events.append((now, tokens))
                    return
                await asyncio.sleep(max(0.5, 60 - (now - self.events[0][0])))


def _est_tokens(text: str) -> int:
    return len(text) // 3 + 50


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])


class OpenAITranslator:
    def __init__(self, base_url: str, api_key: str, model: str, cfg, system_prompt: bool = False):
        if not api_key:
            raise RuntimeError("LLM_API_KEY is not set (see .env.example), or set translate.backend = \"mock\".")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key, self.model, self.cfg, self.system_prompt = api_key, model, cfg, system_prompt
        self.limiter = RateLimiter(cfg.rpm, cfg.tpm)
        self._session: aiohttp.ClientSession | None = None

    async def close(self):
        if self._session:
            await self._session.close()

    async def _chat(self, prompt: str) -> str:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180))
        if self.system_prompt:
            messages = [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": prompt}]
        else:
            messages = [{"role": "user", "content": INSTRUCTIONS + "\n\n" + prompt}]
        body = {"model": self.model, "messages": messages, "temperature": self.cfg.temperature}
        await self.limiter.acquire(_est_tokens(INSTRUCTIONS + prompt) * 2)
        async with self._session.post(self.url, json=body,
                                      headers={"Authorization": f"Bearer {self.key}"}) as r:
            text = await r.text()
            if r.status == 429 or r.status >= 500:
                raise _Retryable(f"HTTP {r.status}: {text[:300]}")
            if r.status != 200:
                raise RuntimeError(f"translation API HTTP {r.status}: {text[:500]}")
            data = json.loads(text)
            return data["choices"][0]["message"]["content"] or ""

    async def translate(self, batch: list[Line], context: list[Line]) -> dict[int, str]:
        prompt = ""
        if context:
            prompt += "Previous lines, for context only (do not translate):\n"
            prompt += json.dumps([{"id": l.id, "en": l.en, "ja": l.ja} for l in context], ensure_ascii=False)
            prompt += "\n\n"
        prompt += "Translate these lines:\n"
        prompt += json.dumps({str(l.id): l.en for l in batch}, ensure_ascii=False)
        want = {l.id for l in batch}
        delay = 2.0
        for attempt in range(self.cfg.max_retries + 1):
            try:
                reply = _extract_json(await self._chat(prompt))
                got = {int(k): str(v).strip() for k, v in reply.items() if str(k).strip().isdigit()}
                missing = want - got.keys()
                if missing:
                    raise _Retryable(f"{len(missing)} of {len(want)} line ids missing in reply")
                return {k: got[k] for k in want}
            except (_Retryable, ValueError, json.JSONDecodeError, aiohttp.ClientError, asyncio.TimeoutError, KeyError) as e:
                if attempt == self.cfg.max_retries:
                    raise RuntimeError(f"translation failed after retries: {e}") from e
                log.warning("translate retry %d: %s", attempt + 1, e)
                await asyncio.sleep(delay + random.random())
                delay = min(delay * 2, 60)
        raise AssertionError


class _Retryable(Exception):
    pass


class MockTranslator:
    """Offline stand-in: plausible-length Japanese-looking text, no API calls."""

    PHRASES = ["はい、そうです。", "ちょっと待って。", "本当に？", "それは違うよ。", "わかりました。",
               "なんでだよ！", "もう一回言って。", "いいね、それ。", "えっと、つまり", "そんなことない。"]

    async def translate(self, batch: list[Line], context: list[Line]) -> dict[int, str]:
        out = {}
        for l in batch:
            n = max(1, round(len(l.en) / 22))
            out[l.id] = "".join(self.PHRASES[(l.id + k) % len(self.PHRASES)] for k in range(n))
        await asyncio.sleep(0.05)
        return out

    async def close(self):
        pass


def make_translator(cfg):
    if cfg.translate.backend == "mock":
        return MockTranslator()
    s = cfg.secrets
    return OpenAITranslator(s.llm_base_url, s.llm_api_key, s.llm_model, cfg.translate, s.llm_system_prompt)
