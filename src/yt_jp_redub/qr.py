"""QR codes of the phone address: printed by `serve` and shown on the library page, so a phone
opens the app by pointing its camera at the screen."""

from __future__ import annotations

from ._vendor.qrcodegen import QrCode

BORDER = 2  # quiet zone, in modules (scanners read 2 fine on a screen)


def _modules(text: str) -> list[list[bool]]:
    """Rows of modules (True = dark), quiet zone included."""
    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    r = range(-BORDER, qr.get_size() + BORDER)
    return [[qr.get_module(x, y) for x in r] for y in r]


def terminal(text: str) -> str:
    """Two module rows per text line, drawn with block characters in the light parts, so the code
    has the right polarity on a dark terminal (the usual one)."""
    rows = _modules(text)
    if len(rows) % 2:
        rows.append([False] * len(rows[0]))
    chars = {(False, False): "█", (False, True): "▀", (True, False): "▄", (True, True): " "}
    return "\n".join(
        "".join(chars[top, bottom] for top, bottom in zip(rows[y], rows[y + 1], strict=True))
        for y in range(0, len(rows), 2)
    )


def svg(text: str) -> str:
    rows = _modules(text)
    path = "".join(f"M{x},{y}h1v1h-1z" for y, row in enumerate(rows) for x, dark in enumerate(row) if dark)
    n = len(rows)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
        f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{path}" fill="#000"/></svg>'
    )
