"""Japanese text as the TTS voice should read it: digits and common symbols spelled out.

The translator is asked not to use them, but a stray "95%" or "10,000" read digit by digit (or
skipped) is jarring, so they are rewritten here before synthesis. Subtitles keep the original text.
"""

from __future__ import annotations

import re

_DIGITS = "〇一二三四五六七八九"
_SMALL = ((1000, "千"), (100, "百"), (10, "十"))
_LARGE = ((10**12, "兆"), (10**8, "億"), (10**4, "万"))
_SYMBOLS = {
    "%": "パーセント",
    "％": "パーセント",
    "&": "アンド",
    "＆": "アンド",
    "+": "プラス",
    "＋": "プラス",
}
_NUMBER = re.compile(r"\d[\d,，]*(?:[.．]\d+)?")
_KANJI_DIGITS = re.compile("[〇一二三四五六七八九]{3,}")  # 一九六〇 is read digit by digit: as a number
_LETTER_NAMES = (  # noqa: SIM905 (one string reads better than 26 list items)
    "エー ビー シー ディー イー エフ ジー エイチ アイ ジェー ケー エル エム "
    "エヌ オー ピー キュー アール エス ティー ユー ブイ ダブリュー エックス ワイ ゼット"
).split()
_LETTERS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", _LETTER_NAMES, strict=True))
# capitals standing alone (AFK, IQ, 12A): spelled out; real English words are left to the voice
_ACRONYM = re.compile(r"(?<![A-Za-z])[A-Z]{1,4}(?![A-Za-z])")


def _below_10000(n: int) -> str:
    out = ""
    for unit, name in _SMALL:
        d, n = divmod(n, unit)
        if d:
            out += ("" if d == 1 else _DIGITS[d]) + name
    return out + (_DIGITS[n] if n else "")


def kanji_number(n: int) -> str:
    """12345 -> 一万二千三百四十五"""
    if n == 0:
        return "ゼロ"
    out = ""
    for unit, name in _LARGE:
        d, n = divmod(n, unit)
        if d:
            out += _below_10000(d) + name
    return out + _below_10000(n)


def _number(m: re.Match) -> str:
    text = m.group().replace(",", "").replace("，", "").replace("．", ".")
    whole, _, frac = text.partition(".")
    # past 兆, read digit by digit
    out = "".join(_DIGITS[int(d)] for d in whole) if len(whole) > 16 else kanji_number(int(whole))
    return out + "点" + "".join(_DIGITS[int(d)] for d in frac) if frac else out


def speakable(text: str) -> str:
    text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    text = _KANJI_DIGITS.sub(lambda m: "".join(str(_DIGITS.index(c)) for c in m.group()), text)
    text = _NUMBER.sub(_number, text)
    text = _ACRONYM.sub(lambda m: "".join(_LETTERS[c] for c in m.group()), text)
    return "".join(_SYMBOLS.get(ch, ch) for ch in text)
