"""Readable control colors derived from the actual composited panel surface."""
from PIL import ImageStat

from .theme import blend


def luminance(color):
    values = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
    return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))


def contrast(a, b):
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)


def panel_palette(image, box):
    crop = image.crop(tuple(round(v) for v in box)).resize((16, 16))
    rgb = ImageStat.Stat(crop).mean[:3]
    surface = "#" + "".join(f"{round(v):02x}" for v in rgb)
    ink = max(("#f4f7fc", "#101827"), key=lambda color: contrast(surface, color))
    if contrast(surface, ink) < 4.5:
        ink = max(("#ffffff", "#000000"), key=lambda color: contrast(surface, color))
    dark = luminance(ink) > .5
    muted = blend(surface, ink, .76)
    if contrast(surface, muted) < 4.5:
        muted = ink
    accent = "#a9c8ff" if dark else "#264fbe"
    if contrast(surface, accent) < 4.5:
        accent = ink
    edge = blend(surface, ink, .28)
    return {"#1c2940": ink, "#697589": muted, "#7b8597": muted,
            "#426de7": accent, "#eaf0ff": blend(surface, accent, .16),
            "#f0f2f6": blend(surface, ink, .09), "#cbd3df": edge,
            "#ccd3df": edge, "#e2e6ed": blend(surface, ink, .14),
            "#b7c0d0": edge, "#c24c43": "#ffaaa0" if dark else "#a02720"}
