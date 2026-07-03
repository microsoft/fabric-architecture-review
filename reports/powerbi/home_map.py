# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Provide the navigation-map background for the report Home page.

The home page is a designed illustration set as the page *background image*;
only the KPI numbers and the navigation buttons are live visuals on top. The
artwork is a premium **Fabric Platform Review** landscape — flowing luminous
silk ribbons across the lower two thirds, glass navigation orbs (one per review
area, each with an icon + label) and a central branded "FABRIC PLATFORM REVIEW"
hub. The top of the canvas stays bright and calm so the banner + KPI scorecards
read cleanly and never overlap the art.

The image is shipped as a static asset (``assets/home_reference.png``) and is
simply loaded (and, if needed, resized to the page) at build time. The same
:data:`NODES` geometry that maps to the orb centres drives the transparent
navigation buttons :mod:`reports.powerbi.report` lays over each orb, so a click
on an orb opens its page.

Pillow is the only dependency and it is present in the Fabric runtime. If it is
missing, or the asset cannot be read, the caller falls back to a plain tile grid.

DATA SAFETY: serves static artwork only; no tenant data is read.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Dict, List, Tuple

# Canvas the artwork is authored against (matches report.PAGE_W/PAGE_H).
PAGE_W, PAGE_H = 1280, 720

# Bundled hero image (1280x720).
HOME_IMAGE = Path(__file__).resolve().parent / "assets" / "home_reference.png"

# The hero image fades to near-white across its top, which reads as bare white
# corners behind the banner + KPI cards. We wash a brand-blue gradient into the
# top band (fully tinted at the very top, easing to fully transparent well above
# the orbs) so the header region reads as a cohesive brand surface, not white.
_HOME_TINT = "#0F6CBD"

_NODE_R = 48  # nav-button hit radius over each orb

# Navigation orbs. Each: destination page ``name`` (MUST match the page name)
# and the orb centre (cx, cy) measured on the 1280x720 hero image. ``cx``/``cy``/
# ``r`` drive the transparent navigation button report.py lays over each orb, so
# a click on the orb opens its page. ``target`` must stay exact.
_SPECS: List[Tuple[str, int, int]] = [
    # target            cx     cy
    ("Overview",        146,  344),
    ("Architecture",    254,  452),
    ("Performance",     379,  337),
    ("Cost",            430,  518),
    ("Governance",      604,  313),
    ("Security",        656,  623),
    ("TenantSettings", 816,  360),
    ("SemanticModels",  893,  544),
    ("ModelDetail",    1029,  409),
    ("Notebooks",      1170,  531),
]


def _build_nodes() -> List[Dict]:
    """Build the orb node list. ``cx``/``cy``/``r`` drive the transparent
    navigation button :mod:`reports.powerbi.report` lays over each orb."""
    return [
        {"target": target, "title": target, "cx": cx, "cy": cy, "r": _NODE_R}
        for target, cx, cy in _SPECS
    ]


NODES: List[Dict] = _build_nodes()


def render(width: int = PAGE_W, height: int = PAGE_H) -> bytes:
    """Return the Fabric Platform Review hero image as PNG bytes.

    Loads the bundled asset and, if its size differs from the requested page
    size, resizes it (high-quality LANCZOS). A brand-blue wash is blended into
    the bright top band so the page corners behind the banner/KPI cards do not
    read as white. Raises if Pillow or the asset is unavailable so the caller
    can fall back to the plain tile grid.
    """
    from PIL import Image

    img = Image.open(HOME_IMAGE).convert("RGB")
    if img.size != (width, height):
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    img = _wash_top(img, width, height)
    # Bake the same rounded azure→brand banner the inside pages use, so the Home
    # "head" matches every other page; report._home_page lays the banner textbox
    # transparently over it.
    base = img.convert("RGBA")
    base.alpha_composite(_banner_overlay(_HOME_TINT, width, height))
    img = base.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _wash_top(img, width: int, height: int):
    """Blend a brand-blue gradient into the bright top band of the hero image so
    the header region (behind the banner + KPI cards) reads as a cohesive brand
    surface instead of white. The wash eases to fully transparent above the orbs
    so the silk ribbons and glass orbs stay untouched."""
    from PIL import Image

    r, g, b = _hex(_HOME_TINT)
    y_full = int(height * 0.045)   # solid brand to here (above + behind banner)
    y_fade = int(height * 0.34)    # fully transparent from here (above the orbs)
    a_max = 216                    # ~0.85: clearly brand, keeps a hint of light
    overlay = Image.new("RGBA", (width, height), (r, g, b, 0))
    px = overlay.load()
    for y in range(min(y_fade, height)):
        if y <= y_full:
            a = a_max
        else:
            t = (y - y_full) / (y_fade - y_full)
            a = int(a_max * (1 - t * t * (3 - 2 * t)))  # smoothstep ease-out
        if a <= 0:
            continue
        for x in range(width):
            px[x, y] = (r, g, b, a)
    base = img.convert("RGBA")
    base.alpha_composite(overlay)
    return base.convert("RGB")


# ---- per-page banner background ------------------------------------------

def _hex(c: str) -> Tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _mix(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


# Banner geometry (matches report._banner: x16,y12 .. PAGE_W-16, h70) and the
# subtle azure -> brand gradient painted into it. The band is sized so the
# centred title + subtitle both sit clear of the rounded bottom corners.
_BANNER_RECT = (16, 12, PAGE_W - 16, 82)
_BANNER_L = "#1E6FD0"   # azure-brand (left)
_BANNER_R = "#0A4A82"   # deep brand (right)


def _banner_overlay(accent: str, width: int = PAGE_W, height: int = PAGE_H,
                    ss: int = 2):
    """A transparent RGBA overlay holding just the rounded azure→brand banner
    and its thin per-page accent line, supersampled (``ss``) for crisp corners.

    Composited onto both the Home hero (:func:`render`) and every non-Home page
    background (:func:`banner_background`) so the "head" is pixel-identical
    everywhere; only the accent-line colour changes per page.
    """
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = (v * ss for v in _BANNER_RECT)
    overlay = Image.new("RGBA", (width * ss, height * ss), (0, 0, 0, 0))
    bw, bh = x1 - x0, y1 - y0
    left, right = _hex(_BANNER_L), _hex(_BANNER_R)
    strip = Image.new("RGB", (bw, bh))
    sp = strip.load()
    for x in range(bw):
        col = _mix(left, right, x / max(1, bw - 1))
        for y in range(bh):
            sp[x, y] = col
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=8 * ss, fill=255)
    overlay.paste(strip, (x0, y0), mask)
    d = ImageDraw.Draw(overlay)
    ly = y1 + 2 * ss
    d.rounded_rectangle((x0, ly, x1, ly + 2 * ss), radius=2 * ss, fill=_hex(accent) + (255,))
    return overlay.resize((width, height), Image.Resampling.LANCZOS)


def banner_background(accent: str, width: int = PAGE_W, height: int = PAGE_H,
                      canvas: str = "#F3F2F1", scale: int = 2) -> bytes:
    """An opaque page background: flat canvas with a subtle azure→brand gradient
    banner (rounded) and a thin ``accent``-coloured category line beneath it.

    Used as the page background on every non-Home page so each banner reads as a
    premium gradient with a small per-page category accent, per the design note.
    """
    from PIL import Image

    base = Image.new("RGBA", (width, height), _hex(canvas) + (255,))
    base.alpha_composite(_banner_overlay(accent, width, height, ss=scale))
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


if __name__ == "__main__":  # pragma: no cover - manual preview
    out = Path("home_map_preview.png")
    out.write_bytes(render())
    print("wrote", out.resolve())
