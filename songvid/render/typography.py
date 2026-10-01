"""Word-timed lyric overlay: serif italic near the bottom, words brighten as they are sung."""

from __future__ import annotations

import glob
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..schemas import Timing

FONT_CANDIDATES = [
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


class LyricLayer:
    def __init__(self, timing: Timing, width: int, height: int, font_path: str | None,
                 bottom: float = 0.87, scale: float = 0.052):
        self.tm, self.W, self.H = timing, width, height
        size = int(height * scale)
        fp = find_font(font_path)
        self.font = ImageFont.truetype(fp, size) if fp else ImageFont.load_default(size)
        self.bottom = bottom
        self.pad = int(size * 0.6)
        self._cache: dict[int, list[Sprite]] = {}
        # Visibility windows that never overlap the next line.
        self.windows = []
        for i, ln in enumerate(timing.lines):
            s = ln.start - LEAD_IN
            e = ln.end + HOLD
            if i + 1 < len(timing.lines):
                e = min(e, timing.lines[i + 1].start - LEAD_IN + FADE * 0.5)
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
                ImageDraw.Draw(im).text((self.pad, self.pad), w.text, font=self.font, fill=255)
                glow = im.filter(ImageFilter.GaussianBlur(self.pad * 0.45))
                sprites.append(Sprite(np.asarray(im, np.float32) / 255, np.asarray(glow, np.float32) / 255,
                                      int(x) - self.pad, y0 + r * lh - self.pad, w.start, w.end))
                x += widths[k] + space
        self._cache[li] = sprites
        return sprites

    def active(self, t: float) -> list[tuple[int, float]]:
        out = []
        for li, (s, e) in enumerate(self.windows):
            if s <= t <= e:
                a = min(1.0, (t - s) / FADE, (e - t) / FADE)
                out.append((li, max(0.0, a)))
        return out

    def draw(self, frame: np.ndarray, t: float, allowed=lambda li: True, color=(1.0, 0.97, 0.92)) -> np.ndarray:
        """Composite onto a float32 HxWx3 0..1 frame in place."""
        col = np.array(color, np.float32)
        for li, line_a in self.active(t):
            if not allowed(li):
                continue
            for sp in self._layout(li):
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
                # dark halo for legibility, then the glyphs, then a warm glow while sung
                region *= (1 - gl * 0.55 * line_a)[..., None]
                region[:] = region * (1 - al * a)[..., None] + col * (al * a)[..., None]
                if singing:
                    region += (gl * 0.35 * line_a)[..., None] * col
        return frame
