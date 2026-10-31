"""English -> natural spoken Japanese through any OpenAI-compatible chat API.

Batches of ~40 lines with preceding lines as context, JSON in and out keyed by
line ID, and a check that every ID comes back. One attempt per call: the caller
(worker/translation.py) schedules retries. Gemma on Google AI Studio
rejects system prompts, so by default the instructions go into the user message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque

import aiohttp

from ..models import Line
from .pace import char_budget

log = logging.getLogger(__name__)

INSTRUCTIONS = """You translate English video dialogue into natural spoken Japanese for a Japanese dub.
The audience is Japanese learners who will listen to the dub, so the Japanese must sound like real people
talking.

Rules:
- Match each speaker's register: casual stays casual (タメ口, contractions, fillers), polite stays polite,
  rude stays rude. Don't make everything です/ます.
- Keep the emotion and personality: jokes, sarcasm, anger, excitement.
- The dub has to fit the original timing. Each line comes with "max_chars": the most Japanese characters
  (kana/kanji, not counting punctuation) that can be spoken in the time the English line takes. Stay within
  it: paraphrase, drop filler words, use shorter natural phrasing. A short natural line beats a complete
  literal one.
- Translate each line on its own (lines are spoken separately), but use the surrounding lines to keep
  pronouns, names and tone consistent.
- Keep names in katakana; well-known brand or game names may stay in Latin letters.
- Fillers like "uh", "um" become natural Japanese fillers (えっと, あの) or are dropped.
- Write numbers the way they'd be spoken naturally; avoid symbols the voice can't read.
- Output ONLY a JSON object mapping every line id (as a string) to its Japanese translation.
  No comments, no markdown.
"""


def char_budgets(lines: list[Line]) -> dict[int, int]:
    """Japanese characters each line can hold: its own time plus up to 0.5 s of the following pause."""
    out = {}
    for i, l in enumerate(lines):
        nxt = lines[i + 1].start if i + 1 < len(lines) else l.end + 0.5
        out[l.id] = char_budget(max(0.2, min(nxt, l.end + 0.5) - l.start))
    return out


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
                if (len(self.events) < self.rpm and used + tokens <= self.tpm) or not self.events:
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
    return json.loads(text[start : end + 1])


_GOOGLE_NATIVE = re.compile(
    r"^(https://generativelanguage\.googleapis\.com/v1\w*)/models/([^/:]+)(?::\w+)?/?$"
)


def normalize_endpoint(base_url: str, model: str) -> tuple[str, str]:
    """LLM_BASE_URL must be an OpenAI-compatible API root. Two common slips are fixed up instead
    of failing: a full .../chat/completions URL, and a Google AI Studio native model URL
    (.../models/<model>:generateContent), which maps to that service's OpenAI-compatible root."""
    url = base_url.strip().strip("\"'")
    if url.upper().startswith("LLM_BASE_URL="):  # the key pasted into its own value
        url = url.split("=", 1)[1].strip().strip("\"'")
    if not re.match(r"^https?://[^\s/]+", url):
        raise RuntimeError(f"LLM_BASE_URL is not a URL: {url!r} (expected e.g. http://host/v1)")
    if m := _GOOGLE_NATIVE.match(url):
        fixed = f"{m.group(1)}/openai/"
        log.warning("LLM_BASE_URL is a Gemini-native URL; using %s with model %s instead", fixed, m.group(2))
        return fixed, m.group(2)
    if url.rstrip("/").endswith("/chat/completions"):
        return url.rstrip("/")[: -len("/chat/completions")], model
    return url, model


class OpenAITranslator:
    def __init__(self, base_url: str, api_key: str, model: str, cfg, system_prompt: bool = False):
        if not api_key:
            raise RuntimeError(
                'LLM_API_KEY is not set (see .env.example), or set translate.backend = "mock".'
            )
        base_url, model = normalize_endpoint(base_url, model)
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key, self.model, self.cfg, self.system_prompt = api_key, model, cfg, system_prompt
        self.limiter = RateLimiter(cfg.rpm, cfg.tpm)
        self._session: aiohttp.ClientSession | None = None
        # reasoning effort goes out in the first shape the API accepts: OpenAI's top-level
        # reasoning_effort, then OpenRouter's reasoning object, then not at all
        effort = str(getattr(cfg, "reasoning_effort", "") or "").strip()
        self._reasoning = (
            [{"reasoning_effort": effort}, {"reasoning": {"effort": effort}}, {}] if effort else [{}]
        )

    async def close(self):
        if self._session:
            await self._session.close()

    async def _chat(self, prompt: str, timeout: float) -> str:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        if self.system_prompt:
            messages = [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": prompt}]
        else:
            messages = [{"role": "user", "content": INSTRUCTIONS + "\n\n" + prompt}]
        await self.limiter.acquire(_est_tokens(INSTRUCTIONS + prompt) * 2)
        while True:
            extra = self._reasoning[0]
            body = {"model": self.model, "messages": messages, "temperature": self.cfg.temperature, **extra}
            async with self._session.post(
                self.url,
                json=body,
                headers={"Authorization": f"Bearer {self.key}"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as r:
                status, text = r.status, await r.text()
            if status == 400 and extra and "reasoning" in text.lower():
                log.warning("translation API rejected %s; trying without it", next(iter(extra)))
                self._reasoning.pop(0)
                continue
            if status == 429 or status >= 500:
                raise Retryable(f"HTTP {status}: {text[:300]}")
            if status != 200:
                raise RuntimeError(f"translation API HTTP {status}: {text[:500]}")
            try:
                return json.loads(text)["choices"][0]["message"]["content"] or ""
            except (ValueError, LookupError, TypeError) as e:
                raise Retryable(f"malformed reply: {text[:300]}") from e

    async def translate(
        self,
        batch: list[Line],
        context: list[Line],
        budgets: dict[int, int] | None = None,
        timeout: float | None = None,
    ) -> dict[int, str]:
        """One attempt; raises Retryable when trying again may help. Retries are scheduled by
        the caller, so a failing batch doesn't hold up the others."""
        prompt = ""
        if context:
            prompt += "Previous lines, for context only (do not translate):\n"
            prompt += json.dumps([{"id": l.id, "en": l.en, "ja": l.ja} for l in context], ensure_ascii=False)
            prompt += "\n\n"
        prompt += "Translate these lines:\n"
        budgets = budgets or {}
        prompt += json.dumps(
            {
                str(l.id): {"en": l.en, "max_chars": budgets[l.id]} if l.id in budgets else {"en": l.en}
                for l in batch
            },
            ensure_ascii=False,
        )
        want = {l.id for l in batch}
        try:
            reply = _extract_json(await self._chat(prompt, timeout or 180))
        except (TimeoutError, ValueError, aiohttp.ClientError) as e:
            raise Retryable(f"{type(e).__name__}: {e}") from e
        got = {int(k): str(v).strip() for k, v in reply.items() if str(k).strip().isdigit()}
        missing = want - got.keys()
        if missing:
            raise Retryable(f"{len(missing)} of {len(want)} line ids missing in reply")
        return {k: got[k] for k in want}


class Retryable(Exception):
    """A failed attempt worth repeating (rate limit, server error, timeout, unusable reply)."""


class MockTranslator:
    """Offline stand-in: plausible-length Japanese-looking text, no API calls."""

    PHRASES = (
        "はい、そうです。",
        "ちょっと待って。",
        "本当に？",
        "それは違うよ。",
        "わかりました。",
        "なんでだよ！",
        "もう一回言って。",
        "いいね、それ。",
        "えっと、つまり",
        "そんなことない。",
    )

    async def translate(
        self,
        batch: list[Line],
        context: list[Line],
        budgets: dict[int, int] | None = None,
        timeout: float | None = None,
    ) -> dict[int, str]:
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
