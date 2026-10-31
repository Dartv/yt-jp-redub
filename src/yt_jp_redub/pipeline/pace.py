"""How long spoken Japanese takes: shared by translation (length budgets) and dubbing (time-fit)."""

from __future__ import annotations

import numpy as np

BASE_SEC = 0.85  # fixed cost of any TTS utterance (measured on IndexTTS-2.5: ~0.9 s)
SEC_PER_CHAR = 0.11  # natural pace per kana/kanji (measured: 0.112 s)
_SILENT = set("、。！？!?…「」『』（）()・ー～〜,.")


def spoken_len(text: str) -> int:
    """Characters that take time to say (punctuation and long-vowel marks excluded)."""
    return sum(1 for ch in text if not ch.isspace() and ch not in _SILENT) or 1


def max_speech_sec(text: str) -> float:
    """The longest a TTS take of `text` may plausibly need: anything longer is the model running
    on (on 1-3 kana interjections IndexTTS tends to babble on, or drift into English)."""
    drawn_out = sum(text.count(ch) for ch in "ー～〜")  # not counted in spoken_len, but they take time
    return 0.6 + 0.2 * (spoken_len(text) + drawn_out)


def char_range(seconds: float, speedup: float = 1.15) -> tuple[int, int]:
    """How many characters fill `seconds` at a natural pace: at most what fits with a little
    speed-up, at least enough that the voice doesn't drag or stop early. Even a short retort gets
    room for a complete form (してない)."""
    most = max(5, int((seconds * speedup - BASE_SEC) / SEC_PER_CHAR))
    return max(2, round(most * 0.6)), most


class PaceModel:
    """Predicts TTS length at duration_factor 1.0 as base + rate * characters.

    The base matters: every utterance carries a roughly fixed onset/decay, so a purely
    proportional model overestimates long lines (which then get sped up and end early)
    and underestimates short ones. Fitted online with exponential forgetting.
    """

    def __init__(self, base: float = BASE_SEC, rate: float = SEC_PER_CHAR, memory: float = 0.97):
        self.base, self.rate, self.memory = base, rate, memory
        # weighted least-squares sums, seeded with a weak prior at 10 characters
        prior_y = base + rate * 10
        self._w, self._sx, self._sy, self._sxx, self._sxy = 2.0, 20.0, 2 * prior_y, 200.0, 20 * prior_y

    def predict(self, text: str) -> float:
        return max(0.3, self.base + self.rate * spoken_len(text))

    def update(self, text: str, natural_sec: float) -> None:
        x, y, m = spoken_len(text), natural_sec, self.memory
        self._w = m * self._w + 1
        self._sx, self._sy = m * self._sx + x, m * self._sy + y
        self._sxx, self._sxy = m * self._sxx + x * x, m * self._sxy + x * y
        mx, my = self._sx / self._w, self._sy / self._w
        var = self._sxx / self._w - mx * mx
        if var > 1.0:
            self.rate = float(np.clip((self._sxy / self._w - mx * my) / var, 0.05, 0.3))
            self.base = float(np.clip(my - self.rate * mx, 0.0, 1.2))
