"""Draw the Plex Picks icon and logo for the Home Assistant brands repository, HACS and the integrations page, and an
icon for the Shortlist add-on folder. PNG only, drawn with Pillow: no fonts, so it renders the same on any machine.

Plex Picks: a dark rounded square with three poster cards in a row, the middle one in Plex amber carrying a play
triangle; a thin amber "progress" line under the row. Reads at 24 px as "a row of posters".

    python brand/make_brand.py   -> custom_components/plex_picks/brand/{icon,icon@2x,logo,logo@2x,dark_logo,dark_logo@2x}.png
                                    addon/shortlist/icon.png, logo.png
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, "addon", "shortlist")
OUT = os.path.join(ROOT, "custom_components", "plex_picks", "brand")   # shipped with the integration (HA 2026.3+ serves it)

DARK = (30, 30, 32, 255)
AMBER = (229, 160, 13, 255)
AMBER_DEEP = (176, 118, 8, 255)
CARD_A = (86, 96, 120, 255)
CARD_B = (120, 86, 110, 255)
WHITE = (255, 255, 255, 255)
INK = (24, 24, 26, 255)


def _poster(d: ImageDraw.ImageDraw, box: list[int], fill: tuple, radius: int) -> None:
    d.rounded_rectangle(box, radius=radius, fill=fill)
    x0, y0, x1, y1 = box
    # a lighter top edge, like light on a card
    d.rounded_rectangle([x0, y0, x1, y0 + max(2, (y1 - y0) // 14)], radius=radius, fill=tuple(min(255, c + 28) for c in fill[:3]) + (255,))


def icon(size: int, background: bool = True) -> Image.Image:
    s = size / 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if background:
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(56 * s), fill=DARK)
    r = int(12 * s)
    # three posters: left, middle (amber, bigger), right
    _poster(d, [int(30 * s), int(66 * s), int(86 * s), int(150 * s)], CARD_A, r)
    _poster(d, [int(170 * s), int(66 * s), int(226 * s), int(150 * s)], CARD_B, r)
    _poster(d, [int(92 * s), int(50 * s), int(164 * s), int(158 * s)], AMBER, int(14 * s))
    # the play triangle on the amber card
    cx, cy = 128 * s, 104 * s
    tri = [(cx - 14 * s, cy - 20 * s), (cx - 14 * s, cy + 20 * s), (cx + 22 * s, cy)]
    d.polygon(tri, fill=INK)
    # the progress line under the row
    d.rounded_rectangle([int(30 * s), int(176 * s), int(226 * s), int(188 * s)], radius=int(6 * s), fill=(70, 70, 74, 255))
    d.rounded_rectangle([int(30 * s), int(176 * s), int(150 * s), int(188 * s)], radius=int(6 * s), fill=AMBER)
    # a small dot after the line: "more to the right"
    d.ellipse([int(198 * s), int(204 * s), int(214 * s), int(220 * s)], fill=AMBER_DEEP)
    d.ellipse([int(174 * s), int(204 * s), int(190 * s), int(220 * s)], fill=(90, 90, 94, 255))
    d.ellipse([int(150 * s), int(204 * s), int(166 * s), int(220 * s)], fill=(90, 90, 94, 255))
    return img


def logo(height: int, dark: bool) -> Image.Image:
    """Icon plus a wordmark drawn as bars (no font): two lines, one amber, like a title and a subtitle."""
    width = int(height * 3.4)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    img.alpha_composite(icon(height), (0, 0))
    d = ImageDraw.Draw(img)
    text = WHITE if dark else INK
    x0 = int(height * 1.2)
    d.rounded_rectangle([x0, int(height * 0.30), x0 + int(height * 2.0), int(height * 0.46)], radius=int(height * 0.08), fill=text)
    d.rounded_rectangle([x0, int(height * 0.56), x0 + int(height * 1.25), int(height * 0.70)], radius=int(height * 0.07), fill=AMBER)
    return img


def addon_icon(size: int) -> Image.Image:
    """Shortlist's add-on tile: an amber rounded square with a short list of three rows, the first ticked."""
    s = size / 128
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(28 * s), fill=AMBER)
    for i, y in enumerate((34, 58, 82)):
        d.rounded_rectangle([int(44 * s), int(y * s), int((104 - i * 14) * s), int((y + 11) * s)], radius=int(5 * s), fill=INK)
        d.ellipse([int(22 * s), int(y * s), int((22 + 12) * s), int((y + 12) * s)], fill=INK)
    # tick on the first row's bullet
    d.line([(int(25 * s), int(40 * s)), (int(28 * s), int(43 * s)), (int(33 * s), int(37 * s))], fill=AMBER, width=max(2, int(2.5 * s)))
    return img


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    icon(256).save(os.path.join(OUT, "icon.png"))
    icon(512).save(os.path.join(OUT, "icon@2x.png"))
    logo(128, dark=False).save(os.path.join(OUT, "logo.png"))
    logo(256, dark=False).save(os.path.join(OUT, "logo@2x.png"))
    logo(128, dark=True).save(os.path.join(OUT, "dark_logo.png"))
    logo(256, dark=True).save(os.path.join(OUT, "dark_logo@2x.png"))
    os.makedirs(ADDON, exist_ok=True)
    addon_icon(128).save(os.path.join(ADDON, "icon.png"))
    lg = Image.new("RGBA", (250, 100), (0, 0, 0, 0))
    lg.alpha_composite(addon_icon(100), (0, 0))
    d = ImageDraw.Draw(lg)
    d.rounded_rectangle([118, 30, 240, 46], radius=8, fill=INK)
    d.rounded_rectangle([118, 56, 200, 70], radius=7, fill=AMBER)
    lg.save(os.path.join(ADDON, "logo.png"))
    print("wrote", OUT, "and", ADDON)


if __name__ == "__main__":
    main()
