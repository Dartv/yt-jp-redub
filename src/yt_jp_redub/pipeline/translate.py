"""English -> natural spoken Japanese through any OpenAI-compatible chat API.

Batches of ~40 lines with preceding lines as context, JSON in and out keyed by
line ID, and a check that every ID comes back. One attempt per call: the caller
(worker/translation.py) schedules retries. Gemma on Google AI Studio
rejects system prompts, so by default the instructions go into the user message.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import aiohttp

from ..models import Line
from .pace import char_range, spoken_len
from .segment import continues, sentences

log = logging.getLogger(__name__)

INSTRUCTIONS = """You translate English video dialogue into natural spoken Japanese for a Japanese dub.
The audience is Japanese learners who will listen to the dub, so the Japanese must sound like real people
talking.

Rules:
- Meaning first: keep what each line says, including the main point of explanations, rules and
  instructions. Never drop content: every part of a line (a condition and its result, a reason, an
  object) is in its Japanese, so "if it rains tomorrow, we'll have to cancel the trip" keeps both
  halves. Get key terms right in context (in an argument, "contradiction" is 否定/反論, not 矛盾), and
  use one translation for a recurring key term throughout.
- The English is an automatic transcript and has mistakes: misheard words, names and phrases. Work out
  what was really said from the context and the video title, and translate that, never the garble.
- Match each speaker's register: casual stays casual (タメ口, contractions, fillers), polite stays polite,
  rude stays rude, a narrator sounds like a narrator. Don't make everything です/ます.
- Keep the emotion and personality: jokes, sarcasm, anger, excitement. Rephrase wordplay so it works in
  Japanese.
- The dub has to fit the original timing. Each line comes with "chars": the range of Japanese characters
  (kana/kanji, not counting punctuation) that can be spoken in the time the English line takes. Stay inside
  it: use the room for complete, natural spoken phrasing (a line far below the range leaves the voice
  dragging or silent, and usually means part of the line was left out), and paraphrase or drop filler
  when it's tight. A complete line a little over its range is better than one missing part of its point.
- Lines often alternate between speakers (one says "Yes, I did", the next "No, you didn't"). Work out who
  says what from the content and keep each speaker's side, polarity and register (a polite clerk stays
  polite, an abusive man stays rude).
- A sentence often runs across several lines (each line but its last has "cont": true). Translate it as one
  sentence and split the Japanese at about the same points, so each line carries its own part: never repeat a
  phrase across lines, and never leave a line without content. The ranges of a sentence's lines add up: move
  words between those lines as Japanese word order needs, as long as the sentence as a whole fits.
- Short retorts get short complete forms that fit their range (した！, 言ってない！, 違うね), said as
  assertions, not questions. Vary repeated retorts the way a real speaker would.
- Everything is read aloud by a Japanese voice: no digits, %, symbols or Latin letters. Write numbers in
  kanji or kana as spoken (九十五パーセント, 六十二台, 一万ドル) and the same way every time, letters in
  katakana (十二のエー), and English names in katakana. Translate English phrases into real Japanese
  rather than katakana calques (警備の皆さん、ありがとう, not サンキューセキュリティ). Never split a number or
  a noun phrase across lines.
- When a glossary of names is given, use its renderings of those names.
- Fillers like "uh", "um" become natural Japanese fillers (えっと, あの) or are dropped; a line that is only
  filler still gets a short one, never an empty string.
- Output ONLY a JSON object mapping every line id (as a string) to its Japanese translation (a plain string).
  No comments, no markdown.
"""


GLOSSARY_INSTRUCTIONS = """List the proper names in this English video transcript (people, channels, places,
companies, products, games, shows, events) with how a Japanese dub should say them: the established Japanese
name where there is one (小国町, モンティ・パイソン), otherwise katakana. The transcript is automatic: names
are often misheard, so give the real name as the key wherever the context or the title makes it clear.
Only proper names: no ordinary words, phrases or concepts (their translation depends on context), no digits
or Latin letters in the Japanese. At most 30 entries.
Output ONLY a JSON object mapping each name to its Japanese rendering.
"""


@dataclass
class Request:
    """One batch of lines to translate, with what the translator should know about them."""

    batch: list[Line]
    before: list[Line] = field(default_factory=list)  # preceding lines (with their translations)
    after: list[Line] = field(default_factory=list)  # following lines (English only)
    budgets: dict[int, tuple[int, int]] = field(default_factory=dict)  # line id -> (fewest, most) chars
    title: str = ""
    glossary: dict[str, str] = field(default_factory=dict)
    urgent: bool = False  # about to be watched: goes ahead of the others
    timeout: float = 180

    def chars(self, l: Line) -> dict:
        """What the prompt says about a line's length: its range, and whether its sentence goes on
        in the next line (the ranges of a sentence's lines add up)."""
        out = {"chars": "{}-{}".format(*self.budgets[l.id])} if l.id in self.budgets else {}
        return {**out, "cont": True} if continues(l) else out

    def misfit(self, sentence: list[Line], ja: dict[int, str]) -> int:
        """How many characters a sentence's Japanese is outside the sum of its lines' ranges,
        beyond a small tolerance. Japanese word order moves words between the lines of a split
        sentence, so only the sentence as a whole has to fit."""
        if any(l.id not in self.budgets or l.id not in ja for l in sentence):
            return 0
        n = sum(spoken_len(ja[l.id]) for l in sentence)
        lo, hi = (sum(self.budgets[l.id][k] for l in sentence) for k in (0, 1))
        # a little long is fine: the dub may speed up more than the range assumes (tts.max_speedup)
        return max(0, lo - 1 - n, n - math.ceil(hi * 1.1) - 1)


def _line_texts(reply: dict) -> dict[int, str]:
    """Line id -> Japanese; a nested {"ja": ...} answer (some models wrap each line) is unwrapped.
    Empty answers are left out, so they count as missing and the line is asked for again."""

    def text(v) -> str:
        if isinstance(v, dict):
            v = v.get("ja") or next((x for x in v.values() if isinstance(x, str)), "")
        return "" if v is None else str(v).strip()

    texts = {int(k): text(v) for k, v in reply.items() if str(k).strip().isdigit()}
    return {k: v for k, v in texts.items() if v}


def char_budgets(lines: list[Line]) -> dict[int, tuple[int, int]]:
    """(fewest, most) Japanese characters for each line: its own time plus up to 0.5 s of the
    following pause."""
    out = {}
    for i, l in enumerate(lines):
        nxt = lines[i + 1].start if i + 1 < len(lines) else l.end + 0.5
        out[l.id] = char_range(max(0.2, min(nxt, l.end + 0.5) - l.start))
    return out


# requests and tokens per minute by API host, for rpm/tpm = 0 in config.toml: Gemma on Google AI
# Studio's free tier, and xAI's published limits for Grok (37 requests per second, 10M tokens per minute)
KNOWN_LIMITS = {
    "generativelanguage.googleapis.com": (25, 14_000),
    "api.x.ai": (2_000, 10_000_000),
}
UNLIMITED = (100_000, 1_000_000_000)


def api_limits(url: str, rpm: int, tpm: int) -> tuple[int, int]:
    """The limits to keep to: as configured, or the host's known ones, or none (the API's own 429
    answers are retried)."""
    known = KNOWN_LIMITS.get(urlsplit(url).hostname or "", UNLIMITED)
    return rpm or known[0], tpm or known[1]


class RateLimiter:
    """Requests and tokens per minute, over a sliding 60 s window. Urgent requests (someone is
    waiting to watch) go ahead of the others, and a request waits only until enough of the window
    has expired for it."""

    def __init__(self, rpm: int, tpm: int):
        self.rpm, self.tpm = max(1, rpm), max(1000, tpm)
        self.events: deque[tuple[float, int]] = deque()
        self.changed = asyncio.Condition()
        self.urgent_waiting = 0

    def _wait(self, tokens: int, now: float) -> float:
        """Seconds until a request of `tokens` fits; 0 when it fits now."""
        while self.events and now - self.events[0][0] >= 60:
            self.events.popleft()
        count, used = len(self.events), sum(t for _, t in self.events)
        if count < self.rpm and used + tokens <= self.tpm:
            return 0.0
        for when, t in self.events:  # oldest first: once enough of them expire, it fits
            count, used = count - 1, used - t
            if count < self.rpm and used + tokens <= self.tpm:
                return when + 60 - now
        return 0.0

    async def acquire(self, tokens: int, urgent: bool = False) -> None:
        tokens = min(tokens, self.tpm)  # a request bigger than the budget still goes, alone
        async with self.changed:
            self.urgent_waiting += urgent
            try:
                while True:
                    now = time.monotonic()
                    wait = self._wait(tokens, now)
                    if wait <= 0 and (urgent or not self.urgent_waiting):
                        self.events.append((now, tokens))
                        self.changed.notify_all()
                        return
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self.changed.wait(), max(0.05, wait) if wait > 0 else None)
            finally:
                self.urgent_waiting -= urgent
                self.changed.notify_all()


def _est_tokens(text: str) -> int:
    """Rough token count: about 4 characters per token for English and JSON, 1 per Japanese character."""
    wide = sum(1 for ch in text if ord(ch) > 0x2E7F)
    return (len(text) - wide) // 4 + wide + 20


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
        self.limiter = RateLimiter(*api_limits(self.url, cfg.rpm, cfg.tpm))
        self._session: aiohttp.ClientSession | None = None
        # reasoning effort goes out in the first shape the API accepts: OpenAI's top-level
        # reasoning_effort, then OpenRouter's reasoning object, then not at all
        self._effort_shapes = [
            lambda e: {"reasoning_effort": e},
            lambda e: {"reasoning": {"effort": e}},
            lambda e: {},
        ]

    async def close(self):
        if self._session:
            await self._session.close()

    async def _chat(
        self, instructions: str, prompt: str, effort: str, timeout: float, urgent: bool = False
    ) -> dict:
        """One request; the reply's JSON object. Raises Retryable when trying again may help."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        if self.system_prompt:
            messages = [{"role": "system", "content": instructions}, {"role": "user", "content": prompt}]
        else:
            messages = [{"role": "user", "content": instructions + "\n\n" + prompt}]
        # what counts against tokens per minute: the request, plus a reply about half as long as the prompt
        await self.limiter.acquire(_est_tokens(instructions + prompt) + _est_tokens(prompt) // 2, urgent)
        while True:
            shape = self._effort_shapes[0]
            extra = shape(effort) if effort else {}
            body = {"model": self.model, "messages": messages, "temperature": self.cfg.temperature, **extra}
            try:
                async with self._session.post(
                    self.url,
                    json=body,
                    headers={"Authorization": f"Bearer {self.key}"},
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as r:
                    status, text = r.status, await r.text()
            except (TimeoutError, aiohttp.ClientError) as e:
                raise Retryable(f"{type(e).__name__}: {e}") from e
            if status == 400 and extra and "reasoning" in text.lower():
                if self._effort_shapes[0] is shape:  # a concurrent request may have dropped it already
                    log.warning("translation API rejected %s; trying without it", next(iter(extra)))
                    self._effort_shapes.pop(0)
                continue
            if status == 429 or status >= 500:
                raise Retryable(f"HTTP {status}: {text[:300]}")
            if status != 200:
                raise RuntimeError(f"translation API HTTP {status}: {text[:500]}")
            try:
                return _extract_json(json.loads(text)["choices"][0]["message"]["content"] or "")
            except (ValueError, LookupError, TypeError) as e:
                raise Retryable(f"malformed reply: {text[:300]}") from e

    @property
    def _effort(self) -> str:
        return str(self.cfg.reasoning_effort or "").strip()

    async def translate(self, req: Request) -> dict[int, str]:
        """One attempt; raises Retryable when trying again may help. Retries are scheduled by
        the caller, so a failing batch doesn't hold up the others."""
        prompt = f"Video title: {req.title}\n\n" if req.title else ""
        if req.glossary:
            prompt += "Glossary:\n" + json.dumps(req.glossary, ensure_ascii=False) + "\n\n"
        if req.before:
            prompt += "Previous lines, for context only (do not translate):\n"
            prompt += json.dumps(
                [{"id": l.id, "en": l.en, "ja": l.ja} for l in req.before], ensure_ascii=False
            )
            prompt += "\n\n"
        prompt += "Translate these lines:\n"
        prompt += json.dumps({str(l.id): {"en": l.en, **req.chars(l)} for l in req.batch}, ensure_ascii=False)
        if req.after:
            prompt += "\n\nNext lines, for context only (do not translate):\n"
            prompt += json.dumps([{"id": l.id, "en": l.en} for l in req.after], ensure_ascii=False)
        got = _line_texts(await self._chat(INSTRUCTIONS, prompt, self._effort, req.timeout, req.urgent))
        missing = {l.id for l in req.batch} - got.keys()
        if missing:
            raise Retryable(f"{len(missing)} of {len(req.batch)} line ids missing in reply")
        return await self._refit(req, {l.id: got[l.id] for l in req.batch})

    async def _refit(self, req: Request, ja: dict[int, str]) -> dict[int, str]:
        """Sentences whose Japanese is well outside their character range are sent back once to be
        reworded (too short drags or stops early in the dub, too long is rushed). A failed or
        worse rewording keeps the first translation."""
        misfits = [s for s in sentences(req.batch) if req.misfit(s, ja) > 0]
        off = [l for s in misfits for l in s]
        if not off:
            return ja
        prompt = (
            "These translations don't fit their timing. Reword each to land inside its character range,\n"
            "keeping its register and its place in the sentence. Too short (has_chars below the range): the\n"
            "translation left out part of what the English says, so put it back; don't pad with filler.\n"
            "Too long: say all of it more compactly; never cut a line off mid-phrase or drop its point.\n"
        )
        prompt += json.dumps(
            {
                str(l.id): {"en": l.en, "ja": ja[l.id], "has_chars": spoken_len(ja[l.id]), **req.chars(l)}
                for l in off
            },
            ensure_ascii=False,
        )
        try:
            redo = _line_texts(await self._chat(INSTRUCTIONS, prompt, self._effort, req.timeout, req.urgent))
        except Retryable as e:
            log.info("rewording %d lines to fit failed: %s", len(off), str(e)[:200])
            return ja
        for sentence in misfits:
            new = {**ja, **redo}  # a reply may reword only part of a sentence
            if req.misfit(sentence, new) < req.misfit(sentence, ja):
                ja.update((l.id, new[l.id]) for l in sentence)
        return ja

    async def glossary(self, title: str, lines: list[Line]) -> dict[str, str]:
        """Japanese renderings of the proper names in the transcript, so batches
        translated in parallel agree with each other."""
        prompt = f"Video title: {title}\n\nTranscript (automatic, may mishear names):\n"
        prompt += "\n".join(l.en for l in lines)
        reply = await self._chat(GLOSSARY_INSTRUCTIONS, prompt, self._effort, 120)
        return {str(k): str(v) for k, v in reply.items() if str(k).strip() and str(v).strip()}


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

    async def translate(self, req: Request) -> dict[int, str]:
        out = {}
        for l in req.batch:
            n = max(1, round(len(l.en) / 22))
            out[l.id] = "".join(self.PHRASES[(l.id + k) % len(self.PHRASES)] for k in range(n))
        await asyncio.sleep(0.05)
        return out

    async def glossary(self, title: str, lines: list[Line]) -> dict[str, str]:
        return {}

    async def close(self):
        pass


def make_translator(cfg):
    if cfg.translate.backend == "mock":
        return MockTranslator()
    s = cfg.secrets
    return OpenAITranslator(s.llm_base_url, s.llm_api_key, s.llm_model, cfg.translate, s.llm_system_prompt)
