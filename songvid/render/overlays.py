"""Broadcast graphics over the picture: chyrons, a news ticker, the LIVE bug, captions, title cards,
and a wrestling-style VS card.

Overlays live on the storyboard (`Storyboard.overlays`) with absolute song times, so a ticker can crawl
across several shots without restarting at each cut. Layout is designed at 1080p and scaled.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..schemas import Overlay

FONTS = Path(__file__).resolve().parent / "fonts"
FALLBACK_SANS = ["/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]

RED = (200, 16, 46)
NAVY = (11, 31, 58)
WHITE = (244, 244, 242)
INK = (14, 14, 18)
AMBER = (232, 160, 32)

CHYRON_TAGS = {  # style -> (kicker text, kicker colour)
    "breaking": ("BREAKING NEWS", RED),
    "update": ("UPDATE", AMBER),
    "developing": ("DEVELOPING", RED),
    "live": ("LIVE", RED),
    "expert": ("EXPERT", NAVY),
}


def font(name: str, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    path = FONTS / name
    if not path.exists():
        path = next((Path(f) for f in FALLBACK_SANS if Path(f).exists()), None)
    if path is None:
        return ImageFont.load_default(size)
    f = ImageFont.truetype(str(path), max(8, size))
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except OSError:
            pass
    return f


def ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def ease_back(t: float) -> float:
    t = min(1.0, max(0.0, t))
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


class Sprite:
    """Premultiplied RGBA as float32 arrays, composited onto a 0..1 float frame."""

    def __init__(self, im: Image.Image):
        a = np.asarray(im.convert("RGBA"), np.float32) / 255
        self.alpha = a[..., 3:4]
        self.rgb = a[..., :3] * self.alpha
        self.h, self.w = self.alpha.shape[:2]

    def draw(self, frame: np.ndarray, x: float, y: float, opacity: float = 1.0, crop_w: int | None = None) -> None:
        if opacity <= 0.001:
            return
        H, W = frame.shape[:2]
        x, y = int(round(x)), int(round(y))
        w = self.w if crop_w is None else max(0, min(self.w, crop_w))
        x1, y1, x2, y2 = max(0, x), max(0, y), min(W, x + w), min(H, y + self.h)
        if x2 <= x1 or y2 <= y1:
            return
        sx, sy = x1 - x, y1 - y
        al = self.alpha[sy:sy + y2 - y1, sx:sx + x2 - x1] * opacity
        rgb = self.rgb[sy:sy + y2 - y1, sx:sx + x2 - x1] * opacity
        region = frame[y1:y2, x1:x2]
        region[:] = region * (1 - al) + rgb


def _text_box(text: str, f: ImageFont.FreeTypeFont, fg, bg, pad_x: int, pad_y: int, min_w: int = 0) -> Image.Image:
    l, t, r, b = f.getbbox(text)
    asc, desc = f.getmetrics()
    w = max(min_w, int(r - l) + pad_x * 2)
    h = asc + desc + pad_y * 2
    im = Image.new("RGBA", (w, h), (*bg, 255) if bg else (0, 0, 0, 0))
    ImageDraw.Draw(im).text((pad_x - l, pad_y), text, font=f, fill=(*fg, 255))
    return im


def _shadowed(im: Image.Image, radius: float, strength: int = 170) -> Image.Image:
    """Add a soft drop shadow so light text reads over any picture."""
    pad = int(radius * 3)
    out = Image.new("RGBA", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    a = im.split()[3].point(lambda v: v * strength // 255)
    sh.paste((0, 0, 0, 255), (pad, pad + int(radius * 0.5)), a)
    out = Image.alpha_composite(out, sh.filter(ImageFilter.GaussianBlur(radius)))
    out.alpha_composite(im, (pad, pad))
    return out


class OverlayLayer:
    def __init__(self, overlays: list[Overlay], width: int, height: int):
        self.ov = sorted(overlays, key=lambda o: o.start)
        self.W, self.H = width, height
        self.s = height / 1080
        self._cache: dict[int, dict] = {}

    def px(self, v: float) -> int:
        return int(round(v * self.s))

    # ---------- builders (once per overlay) ----------

    def _build(self, k: int, o: Overlay) -> dict:
        if k in self._cache:
            return self._cache[k]
        b = getattr(self, f"_build_{o.kind}")(o)
        self._cache[k] = b
        return b

    def _build_chyron(self, o: Overlay) -> dict:
        tag_text, tag_col = CHYRON_TAGS.get(o.style or "breaking", CHYRON_TAGS["breaking"])
        tag_text = (o.tag or tag_text).upper()
        tag = _text_box(tag_text, font("BarlowCondensed-ExtraBold.ttf", self.px(34)), WHITE, tag_col,
                        self.px(18), self.px(6))
        head = _text_box(o.text.upper(), font("BarlowCondensed-Bold.ttf", self.px(66)), INK, WHITE,
                         self.px(24), self.px(4), min_w=self.px(860))
        sub = (_text_box(o.sub.upper(), font("BarlowCondensed-Medium.ttf", self.px(34)), WHITE, NAVY,
                         self.px(24), self.px(6), min_w=head.width) if o.sub else None)
        # thin red rule under the headline, broadcast style
        rule = Image.new("RGBA", (head.width, self.px(5)), (*RED, 255))
        return {"tag": Sprite(tag), "head": Sprite(head), "rule": Sprite(rule), "sub": Sprite(sub) if sub else None}

    def _build_ticker(self, o: Overlay) -> dict:
        f = font("BarlowCondensed-SemiBold.ttf", self.px(36))
        items = [i.upper() for i in (o.items or [o.text])]
        gap, dot = self.px(64), self.px(11)  # a red square between headlines
        h = self.px(58)
        width = int(sum(f.getlength(i) for i in items) + gap * len(items)) + self.px(4)
        strip = Image.new("RGBA", (width, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(strip)
        asc, desc = f.getmetrics()
        y = (h - (asc + desc)) // 2
        x = 0.0
        for item in items:
            d.text((x, y), item, font=f, fill=(*WHITE, 255))
            x += f.getlength(item)
            cx, cy = x + gap / 2, h / 2
            d.rectangle([cx - dot / 2, cy - dot / 2, cx + dot / 2, cy + dot / 2], fill=(*RED, 255))
            x += gap
        bar = Image.new("RGBA", (self.W, h), (*NAVY, 242))
        label = _text_box((o.tag or "LATEST").upper(), font("BarlowCondensed-ExtraBold.ttf", self.px(34)), WHITE, RED,
                          self.px(20), (h - sum(font("BarlowCondensed-ExtraBold.ttf", self.px(34)).getmetrics())) // 2)
        speed = (o.speed or 220) * self.s
        return {"bar": Sprite(bar), "strip": Sprite(strip), "label": Sprite(label), "h": h, "speed": speed,
                "period": strip.width}

    def _build_bug(self, o: Overlay) -> dict:
        live = _text_box((o.tag or "LIVE").upper(), font("BarlowCondensed-ExtraBold.ttf", self.px(30)), WHITE, RED,
                         self.px(14), self.px(4))
        name = _text_box((o.text or "HNN").upper(), font("BarlowCondensed-ExtraBold.ttf", self.px(30)), WHITE, NAVY,
                         self.px(14), self.px(4))
        sub = (_text_box(o.sub.upper(), font("BarlowCondensed-Medium.ttf", self.px(24)), WHITE, None,
                         self.px(2), self.px(2)) if o.sub else None)
        return {"live": Sprite(live), "name": Sprite(name), "sub": Sprite(_shadowed(sub, self.px(3))) if sub else None}

    def _build_caption(self, o: Overlay) -> dict:
        if o.style == "serif":
            f = font("EBGaramond-Italic-VF.ttf", self.px(o.size or 58), weight=500)
            text = o.text
        else:
            f = font("BarlowCondensed-SemiBold.ttf", self.px(o.size or 44))
            text = o.text.upper()
        lines = [_text_box(ln, f, WHITE, None, self.px(4), self.px(2)) for ln in text.split("\n")]
        w = max(i.width for i in lines)
        im = Image.new("RGBA", (w, sum(i.height for i in lines)), (0, 0, 0, 0))
        y = 0
        for i in lines:
            im.alpha_composite(i, ((w - i.width) // 2, y))
            y += i.height
        return {"text": Sprite(_shadowed(im, self.px(7), 210))}

    def _build_card(self, o: Overlay) -> dict:
        W, H = self.W, self.H
        im = Image.new("RGBA", (W, H), (0, 0, 0, 255 if o.style != "scrim" else 150))
        d = ImageDraw.Draw(im)
        f1 = font("EBGaramond-Italic-VF.ttf", self.px(o.size or 92), weight=500)
        f2 = font("BarlowCondensed-Medium.ttf", self.px(30))
        blocks = []
        for ln in o.text.split("\n"):
            blocks.append((ln, f1, (245, 238, 226)))
        sub_lines = [ln for ln in o.sub.split("\n") if ln] if o.sub else []
        heights = [sum(f.getmetrics()) * 1.08 for _, f, _ in blocks] + [sum(f2.getmetrics()) * 1.5 for _ in sub_lines]
        y = (H - sum(heights) - (self.px(36) if sub_lines else 0)) / 2
        for (ln, f, col), h in zip(blocks, heights):
            d.text((W / 2, y), ln, font=f, fill=(*col, 255), anchor="ma")
            y += h
        y += self.px(36)
        for ln in sub_lines:
            spaced = " ".join(ln.upper())  # wide tracking, the way film credits do it
            d.text((W / 2, y), spaced, font=f2, fill=(190, 182, 168, 255), anchor="ma")
            y += sum(f2.getmetrics()) * 1.5
        return {"card": Sprite(im)}

    def _build_vs(self, o: Overlay) -> dict:
        big = font("BarlowCondensed-ExtraBold.ttf", self.px(300))
        vs = Image.new("RGBA", (self.px(520), self.px(380)), (0, 0, 0, 0))
        d = ImageDraw.Draw(vs)
        d.text((vs.width / 2, vs.height / 2), "VS", font=big, fill=(*WHITE, 255), anchor="mm",
               stroke_width=self.px(10), stroke_fill=(*RED, 255))
        vs = _shadowed(vs, self.px(18), 230)

        def plate(name: str, sub: str, col) -> Image.Image:
            n = _text_box(name.upper(), font("BarlowCondensed-ExtraBold.ttf", self.px(88)), WHITE, col,
                          self.px(30), self.px(2))
            if not sub:
                return n
            s = _text_box(sub.upper(), font("BarlowCondensed-SemiBold.ttf", self.px(34)), INK, WHITE,
                          self.px(30), self.px(6), min_w=n.width)
            im = Image.new("RGBA", (max(n.width, s.width), n.height + s.height), (0, 0, 0, 0))
            im.alpha_composite(n, (0, 0))
            im.alpha_composite(s, (0, n.height))
            return im

        return {"vs": Sprite(vs), "left": Sprite(plate(o.text, o.sub, RED)),
                "right": Sprite(plate(o.text2, o.sub2, NAVY))}

    # ---------- per-frame ----------

    def draw(self, frame: np.ndarray, t: float, bottom_inset: int = 0, top_inset: int = 0) -> np.ndarray:
        """Draw every overlay active at time t. Insets keep lower thirds clear of letterbox bars."""
        for k, o in enumerate(self.ov):
            if o.start > t:
                break
            if t > o.end:
                continue
            b = self._build(k, o)
            lt, rem = t - o.start, o.end - t
            getattr(self, f"_draw_{o.kind}")(frame, b, o, lt, rem, bottom_inset, top_inset)
        return frame

    def _ticker_h(self, t: float) -> int:
        on = [o for o in self.ov if o.kind == "ticker" and o.start <= t <= o.end]
        return self.px(58) if on else 0

    def _draw_chyron(self, frame, b, o, lt, rem, bottom, top):
        x0 = self.px(96)
        ticker = self._ticker_h(o.start + lt)
        sub_h = b["sub"].h if b["sub"] else 0
        y_head = self.H - max(bottom, ticker) - self.px(44) - sub_h - b["head"].h
        out = ease_out(rem / 0.3) if rem < 0.3 else 1.0
        # kicker slides in, headline wipes open after it, sub fades in last
        tag_in = ease_out(lt / 0.28)
        b["tag"].draw(frame, x0 - (1 - tag_in) * (b["tag"].w + x0), y_head - b["tag"].h, out)
        wipe = ease_out((lt - 0.12) / 0.4)
        cw = int(b["head"].w * wipe * out)
        b["head"].draw(frame, x0, y_head, 1.0, crop_w=cw)
        b["rule"].draw(frame, x0, y_head + b["head"].h - b["rule"].h, 1.0, crop_w=cw)
        if b["sub"]:
            b["sub"].draw(frame, x0, y_head + b["head"].h, ease_out((lt - 0.4) / 0.3) * out, crop_w=cw)

    def _draw_ticker(self, frame, b, o, lt, rem, bottom, top):
        a = min(1.0, lt / 0.3, rem / 0.3)
        y = self.H - bottom - b["h"]
        b["bar"].draw(frame, 0, y, a)
        off = (lt * b["speed"]) % b["period"]
        x = b["label"].w + self.px(16) - off
        while x < self.W:
            b["strip"].draw(frame, x, y, a)
            x += b["period"]
        b["label"].draw(frame, 0, y, a)

    def _draw_bug(self, frame, b, o, lt, rem, bottom, top):
        a = min(1.0, lt / 0.25, rem / 0.25)
        x, y = self.px(60), top + self.px(46)
        b["live"].draw(frame, x, y, a)
        b["name"].draw(frame, x + b["live"].w, y, a)
        if b["sub"]:
            b["sub"].draw(frame, x - self.px(9), y + b["live"].h + self.px(2), a)

    def _draw_caption(self, frame, b, o, lt, rem, bottom, top):
        a = min(1.0, lt / 0.35, rem / 0.35)
        cx, cy = o.pos or (0.5, 0.82)
        sp = b["text"]
        y = cy * self.H - sp.h / 2
        y = min(y, self.H - bottom - sp.h)
        y = max(y, top)
        sp.draw(frame, cx * self.W - sp.w / 2, y, a)

    def _draw_card(self, frame, b, o, lt, rem, bottom, top):
        fade = o.fade if o.fade is not None else 0.6
        a = min(1.0, lt / fade if fade else 1.0, rem / fade if fade else 1.0)
        b["card"].draw(frame, 0, 0, a)

    def _draw_vs(self, frame, b, o, lt, rem, bottom, top):
        out = min(1.0, rem / 0.25)
        y = self.H - max(bottom, self._ticker_h(o.start + lt)) - self.px(60)
        L, R = b["left"], b["right"]
        sl = ease_back(lt / 0.45)
        b["left"].draw(frame, -L.w + sl * (L.w + self.px(80)), y - L.h, out)
        sr = ease_back((lt - 0.15) / 0.45)
        b["right"].draw(frame, self.W - sr * (R.w + self.px(80)), y - R.h, out)
        # VS punches in from big to its size, with a white flash on landing
        p = ease_out((lt - 0.3) / 0.25)
        if p > 0:
            sp = b["vs"]
            scale = 1 + 1.2 * (1 - p)
            if abs(scale - 1) > 0.01:
                im = Image.fromarray((np.concatenate([sp.rgb / np.maximum(sp.alpha, 1e-4), sp.alpha], -1) * 255)
                                     .clip(0, 255).astype(np.uint8), "RGBA")
                im = im.resize((int(sp.w * scale), int(sp.h * scale)), Image.BILINEAR)
                sp = Sprite(im)
            sp.draw(frame, (self.W - sp.w) / 2, (self.H - sp.h) / 2 - self.px(40), p * out)
            flash = max(0.0, 1 - (lt - 0.55) / 0.18) if lt > 0.55 else 0.0
            if flash > 0:
                frame += 0.35 * flash
                np.clip(frame, 0, 1, out=frame)


def ticker_period_seconds(o: Overlay, height: int) -> float:
    """How long one full pass of the ticker text takes (useful when timing a joke in the crawl)."""
    layer = OverlayLayer([o], int(height * 16 / 9), height)
    b = layer._build(0, o)
    return b["period"] / b["speed"] if b["speed"] else math.inf
