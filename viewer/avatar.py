# SPDX-License-Identifier: AGPL-3.0-or-later
"""Avatars a user makes themself: a small SVG drawn from a few values
(``style``, ``seed``, ``hue``), so nothing is uploaded and nothing leaves
the container.

- ``pattern``: a symmetric 5×5 pattern, like a GitHub identicon;
- ``initials``: the first letters of the name on a coloured disc;
- ``shapes``: a few overlapping circles and squares.

Without one of their own, a user gets ``initials`` from their name. At
``/account`` they pick a style and press *New* until they like one.
"""
from __future__ import annotations

import hashlib
import random
import re
import secrets
from html import escape

STYLES = ("pattern", "initials", "shapes")


def default(name: str) -> dict:
    h = int(hashlib.sha256(name.lower().encode()).hexdigest(), 16)
    return {"style": "initials", "seed": format(h % 16 ** 8, "08x"), "hue": h % 360}


def fresh(style: str) -> dict:
    """A new random avatar of ``style``."""
    return {"style": style if style in STYLES else "pattern",
            "seed": secrets.token_hex(4), "hue": secrets.randbelow(360)}


def valid(avatar) -> dict | None:
    """``avatar`` if it is one this module draws, else None."""
    if not isinstance(avatar, dict):
        return None
    style, seed, hue = avatar.get("style"), avatar.get("seed"), avatar.get("hue")
    if style not in STYLES or not isinstance(seed, str) or not re.fullmatch(r"[0-9a-f]{8}", seed):
        return None
    if not isinstance(hue, int) or isinstance(hue, bool) or not 0 <= hue < 360:
        return None
    return {"style": style, "seed": seed, "hue": hue}


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s._@+-]+", name) if p]
    letters = "".join(p[0] for p in parts[:2]) or name[:1] or "?"
    return letters.upper()


def svg(name: str, avatar: dict | None) -> str:
    """The avatar as an SVG document (64×64, scales to any size)."""
    a = valid(avatar) or default(name)
    rnd = random.Random(int(a["seed"], 16))
    hue = a["hue"]
    bg, fg = f"hsl({hue},45%,88%)", f"hsl({hue},55%,38%)"
    body = ""
    if a["style"] == "pattern":
        cells = []
        for y in range(5):
            for x in range(3):
                if rnd.random() < 0.5:
                    for xx in {x, 4 - x}:
                        cells.append(f'<rect x="{6 + xx * 10.4:.1f}" y="{6 + y * 10.4:.1f}" '
                                     'width="10.4" height="10.4"/>')
        body = f'<g fill="{fg}">{"".join(cells)}</g>'
    elif a["style"] == "initials":
        body = (f'<text x="32" y="33" text-anchor="middle" dominant-baseline="central" '
                f'font-family="system-ui,sans-serif" font-size="26" font-weight="600" '
                f'fill="{fg}">{escape(_initials(name))}</text>')
    else:
        shapes = []
        for _ in range(4):
            h2 = (hue + rnd.choice((0, 30, 60, 180, 210))) % 360
            fill = f"hsla({h2},60%,{rnd.randint(40, 65)}%,.75)"
            x, y, r = rnd.randint(10, 54), rnd.randint(10, 54), rnd.randint(8, 20)
            if rnd.random() < 0.5:
                shapes.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}"/>')
            else:
                shapes.append(f'<rect x="{x - r}" y="{y - r}" width="{2 * r}" height="{2 * r}" '
                              f'rx="3" fill="{fill}" transform="rotate({rnd.randint(0, 89)} {x} {y})"/>')
        body = "".join(shapes)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
            f'<clipPath id="c"><circle cx="32" cy="32" r="32"/></clipPath>'
            f'<g clip-path="url(#c)"><rect width="64" height="64" fill="{bg}"/>{body}</g></svg>')
