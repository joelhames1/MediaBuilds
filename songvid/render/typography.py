"""Word-timed lyric overlay: serif italic near the bottom, words brighten as they are sung."""

from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..lyrics import norm
from ..schemas import Timing

BUNDLED_SERIF = str(Path(__file__).resolve().parent / "fonts" / "EBGaramond-Italic-VF.ttf")
FONT_CANDIDATES = [
    BUNDLED_SERIF,
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
    "/Library/Fonts/Georgia Italic.ttf",
    "C:/Windows/Fonts/georgiai.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Georgia_Italic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
]

LEAD_IN = 0.35     # line appears this long before its first word
HOLD = 0.7         # and lingers this long after its last word
FADE = 0.25
DIM = 0.32         # alpha of not-yet-sung words
RAMP = 0.12        # seconds for a word to brighten


def resolve_style(style: str | None, timing_source: str) -> str:
    if style in ("words", "lines"):
        return style
    return "lines" if "line-level" in timing_source and "Whisper" not in timing_source else "words"


def find_font(path: str | None) -> str | None:
    for cand in ([path] if path else []) + FONT_CANDIDATES:
        hits = glob.glob(cand)
        if hits:
            return hits[0]
    return None


@dataclass
class Sprite:
    alpha: np.ndarray  # HxW float 0..1
    glow: np.ndarray
    x: int
    y: int
    start: float
    end: float
    bar: bool = False  # censored word: drawn as a black bar


def load_font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    fp = find_font(path)
    if not fp:
        return ImageFont.load_default(size)
    f = ImageFont.truetype(fp, size)
    if fp == BUNDLED_SERIF:
        f.set_variation_by_axes([500])  # Medium reads better than Regular over moving pictures
    return f


class LyricLayer:
    def __init__(self, timing: Timing, width: int, height: int, font_path: str | None,
                 bottom: float = 0.87, scale: float = 0.052, style: str = "words", censor: set[str] | None = None):
        self.tm, self.W, self.H = timing, width, height
        self.style = style
        self.censor = {norm(w) for w in (censor or set())}
        size = int(height * scale)
        self.font = load_font(font_path, size)
        self.bottom = bottom
        self.pad = int(size * 0.6)
        self._cache: dict[int, list[Sprite]] = {}
        # Visibility windows that never overlap the next line.
        self.windows = []
        for i, ln in enumerate(timing.lines):
            nxt = timing.lines[i + 1].start if i + 1 < len(timing.lines) else None
            if style == "lines":
                # whole lines: arrive on the sung start, stay readable until the next line takes over
                s = ln.start - 0.12
                e = ln.end + 1.6
                if nxt is not None:
                    e = min(e, nxt - 0.12)
                self.windows.append((s, max(e, s + 0.8)))
                continue
            s = ln.start - LEAD_IN
            e = ln.end + HOLD
            if nxt is not None:
                e = min(e, nxt - LEAD_IN + FADE * 0.5)
            self.windows.append((s, max(e, ln.end + 0.1)))

    def _layout(self, li: int) -> list[Sprite]:
        if li in self._cache:
            return self._cache[li]
        ln = self.tm.lines[li]
        words = [self.tm.words[i] for i in ln.words]
        space = self.font.getlength(" ")
        widths = [self.font.getlength(w.text) for w in words]
        rows, cur, cur_w = [], [], 0.0
        max_w = self.W * 0.8
        for k, wd in enumerate(widths):
            if cur and cur_w + space + wd > max_w:
                rows.append(cur)
                cur, cur_w = [], 0.0
            cur_w += (space if cur else 0) + wd
            cur.append(k)
        rows.append(cur)
        asc, desc = self.font.getmetrics()
        lh = int((asc + desc) * 1.15)
        y0 = int(self.H * self.bottom - lh * len(rows))
        sprites = []
        for r, row in enumerate(rows):
            row_w = sum(widths[k] for k in row) + space * (len(row) - 1)
            x = (self.W - row_w) / 2
            for k in row:
                w = words[k]
                bw, bh = int(widths[k]) + self.pad * 2, asc + desc + self.pad * 2
                im = Image.new("L", (bw, bh), 0)
                bar = norm(w.text) in self.censor
                if bar:  # broadcast-standards black bar where the word would be
                    m = int(self.pad * 0.3)
                    ImageDraw.Draw(im).rectangle([self.pad - m, self.pad + int(asc * 0.2), bw - self.pad + m,
                                                  self.pad + asc + int(desc * 0.6)], fill=255)
                    glow = Image.new("L", (bw, bh), 0)
                else:
                    ImageDraw.Draw(im).text((self.pad, self.pad), w.text, font=self.font, fill=255)
                    glow = im.filter(ImageFilter.GaussianBlur(self.pad * 0.45))
                sprites.append(Sprite(np.asarray(im, np.float32) / 255, np.asarray(glow, np.float32) / 255,
                                      int(x) - self.pad, y0 + r * lh - self.pad, w.start, w.end, bar))
                x += widths[k] + space
        self._cache[li] = sprites
        return sprites

    def active(self, t: float) -> list[tuple[int, float]]:
        out = []
        for li, (s, e) in enumerate(self.windows):
            if s <= t <= e:
                fade = 0.12 if self.style == "lines" else FADE
                a = min(1.0, (t - s) / fade, (e - t) / FADE)
                out.append((li, max(0.0, a)))
        return out

    def draw(self, frame: np.ndarray, t: float, allowed=lambda li: True, color=(1.0, 0.97, 0.92)) -> np.ndarray:
        """Composite onto a float32 HxWx3 0..1 frame in place."""
        col = np.array(color, np.float32)
        for li, line_a in self.active(t):
            if not allowed(li):
                continue
            for sp in self._layout(li):
                if self.style == "lines":
                    a, singing = line_a, False
                else:
                    sung = np.clip((t - sp.start) / RAMP, 0, 1)
                    a = line_a * (DIM + (1 - DIM) * sung)
                    singing = sp.start <= t <= sp.end + 0.1
                h, w = sp.alpha.shape
                y1, x1 = max(0, sp.y), max(0, sp.x)
                y2, x2 = min(self.H, sp.y + h), min(self.W, sp.x + w)
                if y2 <= y1 or x2 <= x1:
                    continue
                al = sp.alpha[y1 - sp.y:y2 - sp.y, x1 - sp.x:x2 - sp.x]
                gl = sp.glow[y1 - sp.y:y2 - sp.y, x1 - sp.x:x2 - sp.x]
                region = frame[y1:y2, x1:x2]
                if sp.bar:
                    region *= (1 - al * line_a)[..., None]
                    continue
                # dark halo for legibility, then the glyphs, then a warm glow while sung
                region *= (1 - gl * 0.55 * line_a)[..., None]
                region[:] = region * (1 - al * a)[..., None] + col * (al * a)[..., None]
                if singing:
                    region += (gl * 0.35 * line_a)[..., None] * col
        return frame
