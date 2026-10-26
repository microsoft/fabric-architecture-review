# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Original FAR report artwork rendered with the standard library only.

FUAM-style pages sit on a soft gradient with one large white rounded panel and a
header band. The PNGs are generated deterministically (no Pillow, no binary
assets in the repository) in the same teal family as the report theme.
"""
from __future__ import annotations

import struct
import zlib
from functools import lru_cache
from typing import Callable

WIDTH, HEIGHT = 1920, 1080
PANEL = (11, 73, 1909, 1068)
HERO = (24, 90, 1896, 318)
_TOP, _BOTTOM = (230, 244, 241), (251, 247, 238)
_WHITE = (255, 255, 255)
_SAMPLES = 4


def _png(width: int, height: int, rows: list[bytes], color_type: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def _coverage(px: int, py: int, rect: tuple[int, int, int, int], radius: int) -> float:
    """Anti-aliased coverage of pixel (px, py) by a rounded rectangle."""
    x0, y0, x1, y1 = rect
    hit = 0
    for sy in range(_SAMPLES):
        for sx in range(_SAMPLES):
            x = px + (sx + 0.5) / _SAMPLES
            y = py + (sy + 0.5) / _SAMPLES
            cx = min(max(x, x0 + radius), x1 - radius)
            cy = min(max(y, y0 + radius), y1 - radius)
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                hit += 1
    return hit / (_SAMPLES * _SAMPLES)


def _paint(rows: list[bytearray], rect: tuple[int, int, int, int], radius: int,
           paint: Callable[[int], tuple[int, int, int]]) -> None:
    x0, y0, x1, y1 = rect
    segment = b"".join(bytes(paint(x)) for x in range(x0, x1))
    for y in range(y0, y1):
        row = rows[y]
        if y0 + radius <= y < y1 - radius:
            row[x0 * 3:x1 * 3] = segment
            continue
        for x in list(range(x0, x0 + radius)) + list(range(x1 - radius, x1)):
            alpha = _coverage(x, y, rect, radius)
            if alpha:
                base = tuple(row[x * 3:x * 3 + 3])
                row[x * 3:x * 3 + 3] = bytes(_mix(base, paint(x), alpha))  # type: ignore[arg-type]
        mid0, mid1 = x0 + radius, x1 - radius
        row[mid0 * 3:mid1 * 3] = segment[(mid0 - x0) * 3:(mid1 - x0) * 3]


def _canvas() -> list[bytearray]:
    rows = []
    for y in range(HEIGHT):
        colour = bytes(_mix(_TOP, _BOTTOM, y / (HEIGHT - 1)))
        rows.append(bytearray(colour * WIDTH))
    _paint(rows, PANEL, 14, lambda _x: _WHITE)
    for index, (colour, half) in enumerate((((158, 227, 216), 16), ((60, 181, 168), 18), ((36, 138, 133), 20))):
        top = 16 + index * 12
        _paint(rows, (44 - half, top, 44 + half, top + 10), 4, lambda _x, c=colour: c)
    return rows


@lru_cache(maxsize=None)
def content_background() -> bytes:
    """Header band plus a white rounded content panel for FUAM-style content pages."""
    return _png(WIDTH, HEIGHT, [bytes(row) for row in _canvas()], 2)


@lru_cache(maxsize=None)
def home_background() -> bytes:
    """Content background with a teal hero band behind the Home title."""
    rows = _canvas()
    left, right, mid = (29, 111, 107), (47, 167, 156), (36, 138, 133)
    x0, _, x1, _ = HERO

    def gradient(x: int) -> tuple[int, int, int]:
        t = (x - x0) / (x1 - x0)
        return _mix(left, mid, t * 2) if t < 0.5 else _mix(mid, right, (t - 0.5) * 2)

    _paint(rows, HERO, 14, gradient)
    return _png(WIDTH, HEIGHT, [bytes(row) for row in rows], 2)


@lru_cache(maxsize=None)
def home_icon() -> bytes:
    """Simple teal house glyph used by the Home navigation button (RGBA)."""
    size, samples = 96, 4

    def inside(u: float, v: float) -> bool:
        roof = 0.12 <= v <= 0.52 and abs(u - 0.5) <= (v - 0.12) * 1.05
        body = 0.2 <= u <= 0.8 and 0.46 <= v <= 0.88
        door = 0.42 <= u <= 0.58 and 0.62 <= v <= 0.88
        return (roof or body) and not door

    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            hit = sum(inside((px + (sx + .5) / samples) / size, (py + (sy + .5) / samples) / size)
                      for sy in range(samples) for sx in range(samples))
            row += bytes((36, 138, 133, round(255 * hit / (samples * samples))))
        rows.append(bytes(row))
    return _png(size, size, rows, 6)
