"""Procedural, audio-reactive scenes (numpy). Each scene is a pure function of time,
so frames can be rendered in any order and in parallel.

A scene returns float32 RGB in [0, ~1.5] (HDR-ish; bloom and tonemap happen later).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from ..schemas import Shot

TEX = 256


def _fbm_texture(rng: np.random.Generator, octaves: int = 5) -> np.ndarray:
    """Tileable fractal noise, 0..1."""
    out = np.zeros((TEX, TEX), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        sigma = TEX / (4 * 2 ** o)
        layer = ndimage.gaussian_filter(rng.standard_normal((TEX, TEX)).astype(np.float32), sigma, mode="wrap")
        layer /= layer.std() + 1e-9
        out += amp * layer
        total += amp
        amp *= 0.55
    out /= total
    return ((out - out.min()) / (out.max() - out.min())).astype(np.float32)


def palette_lut(colors: list[str]) -> np.ndarray:
    rgb = []
    for c in colors:
        c = c.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        rgb.append([int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)])
    rgb = np.array(rgb, np.float32) ** 2.2  # to linear
    xs = np.linspace(0, 1, len(rgb))
    t = np.linspace(0, 1, 256)
    return np.stack([np.interp(t, xs, rgb[:, k]) for k in range(3)], axis=1).astype(np.float32)


def apply_lut(v: np.ndarray, lut: np.ndarray) -> np.ndarray:
    idx = np.clip(v * 255, 0, 255).astype(np.int32)
    return lut[idx]


def smoothstep(a: float, b: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


@dataclass
class Frame:
    t: float          # song time (s)
    lt: float         # time since shot start (s)
    f: dict           # audio features at this frame (floats 0..1)
    shot: Shot


class SceneKit:
    """Shared precomputed state for one render resolution."""

    def __init__(self, width: int, height: int, seed: int = 7):
        self.W, self.H = width, height
        rng = np.random.default_rng(seed)
        self.tex = [_fbm_texture(rng) for _ in range(4)]
        aspect = width / height
        ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
        self.x = (xs / width * 2 - 1) * aspect
        self.y = ys / height * 2 - 1
        self.r = np.sqrt(self.x ** 2 + self.y ** 2)
        self.aspect = aspect
        # stars
        stars = np.zeros((height, width), np.float32)
        n = int(width * height / 900)
        sy, sx = rng.integers(0, height, n), rng.integers(0, width, n)
        stars[sy, sx] = rng.random(n) ** 3
        self.stars = ndimage.gaussian_filter(stars, 0.7) * 4
        self.star_phase = rng.random((height, width)).astype(np.float32) * 6.28
        # particles (x, y in a tunnel, z depth) and embers
        self.p_xyz = np.stack([rng.uniform(-3, 3, 2200), rng.uniform(-2, 2, 2200), rng.uniform(0, 1, 2200)], 1)
        self.p_hue = rng.random(2200)
        self.e_x = rng.uniform(-1, 1, 700)
        self.e_y = rng.uniform(0, 1, 700)
        self.e_speed = rng.uniform(0.6, 1.4, 700)
        self.e_size = rng.random(700) ** 2
        self._luts: dict[tuple, np.ndarray] = {}

    def lut(self, colors: list[str]) -> np.ndarray:
        key = tuple(colors)
        if key not in self._luts:
            self._luts[key] = palette_lut(colors)
        return self._luts[key]

    def sample(self, k: int, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        """Bilinear wrap sampling of texture k at (u, v) in texture units (1.0 = one tile)."""
        coords = np.stack([v * TEX, u * TEX])
        return ndimage.map_coordinates(self.tex[k], coords, order=1, mode="grid-wrap")

    def splat(self, px: np.ndarray, py: np.ndarray, w: np.ndarray) -> np.ndarray:
        img = np.zeros((self.H, self.W), np.float32)
        ok = (px >= 0) & (px < self.W) & (py >= 0) & (py < self.H)
        np.add.at(img, (py[ok].astype(int), px[ok].astype(int)), w[ok])
        return img

    # ---------------- scenes ----------------

    def nebula(self, fr: Frame) -> np.ndarray:
        sh, f = fr.shot, fr.f
        sp = 0.01 + 0.04 * sh.speed
        d = fr.t * sp
        z = 1.0 + 0.04 * fr.lt  # slow push in
        x, y = self.x / z, self.y / z
        warp = self.sample(2, x * 0.25 + d * 0.5, y * 0.25) - 0.5
        n1 = self.sample(0, x * 0.35 + d + warp * 0.5, y * 0.35 + warp * 0.5)
        n2 = self.sample(1, x * 0.8 - d * 1.6, y * 0.8 + d * 0.4)
        v = smoothstep(0.2, 0.85, n1 * 0.72 + n2 * 0.28)
        lift = 0.55 + 0.35 * f["energy"] + 0.35 * sh.intensity * f["rms"]
        col = apply_lut(v ** 1.5 * (0.5 + 0.45 * lift), self.lut(sh.palette)) * (0.35 + 0.5 * lift)
        tw = 0.6 + 0.4 * np.sin(self.star_phase + fr.t * 2.0)
        col += (self.stars * tw * (1 - v) * (0.5 + f["high"]))[..., None]
        return col

    def smoke(self, fr: Frame) -> np.ndarray:
        sh, f = fr.shot, fr.f
        d = fr.t * (0.006 + 0.025 * sh.speed)
        x, y = self.x * 0.45, self.y * 0.45 + d * 0.6
        q1 = self.sample(1, x + d, y) - 0.5
        q2 = self.sample(2, x - d * 0.7 + 3.1, y + 1.7) - 0.5
        w1 = self.sample(3, x + q1 * 1.4, y + q2 * 1.4) - 0.5
        v = self.sample(0, x + w1 * 1.2 + q2 * 0.4, y + w1 * 1.2)
        v = smoothstep(0.35, 0.95, v) ** 1.6
        glow = np.exp(-self.r ** 2 * 0.9)
        lift = 0.45 + 0.4 * f["energy"] + 0.4 * sh.intensity * f["rms"]
        col = apply_lut(np.clip(v * (0.7 + 0.5 * glow) * (0.6 + 0.5 * lift), 0, 1), self.lut(sh.palette))
        return col * (0.3 + 0.6 * lift)

    def rays(self, fr: Frame) -> np.ndarray:
        sh, f = fr.shot, fr.f
        sx, sy = 0.25 * np.sin(fr.t * 0.05), -1.35
        dx, dy = self.x - sx, self.y - sy
        ang = np.arctan2(dx, dy)  # 0 = straight down
        dist = np.sqrt(dx * dx + dy * dy)
        u = ang * 1.3 + fr.t * 0.012 * (0.5 + sh.speed)
        beams = self.sample(0, u, np.full_like(u, fr.t * 0.01)) * 0.6 + self.sample(1, u * 2.3, np.full_like(u, 0.3 + fr.t * 0.017)) * 0.4
        beams = smoothstep(0.42, 0.9, beams) ** 1.5
        falloff = np.exp(-dist * (0.55 - 0.15 * sh.intensity)) * np.clip(1.4 - np.abs(ang) * 1.1, 0, 1)
        haze = self.sample(2, self.x * 0.3 + fr.t * 0.01, self.y * 0.3) * 0.35
        lift = 0.7 + 0.5 * f["energy"] + 0.6 * sh.intensity * (0.6 * f["rms"] + 0.4 * f["beat"])
        v = np.clip(beams * falloff * (0.75 + 0.6 * lift) + haze * falloff * 0.6, 0, 1)
        col = apply_lut(v, self.lut(sh.palette)) * (0.7 + 0.7 * lift)
        col += (np.exp(-dist * 3.5) * 1.2 * lift)[..., None] * self.lut(sh.palette)[-1]
        return col

    def particles(self, fr: Frame) -> np.ndarray:
        sh, f = fr.shot, fr.f
        travel = f["travel"] * (0.08 + 0.2 * sh.speed)
        x, y, z0 = self.p_xyz.T
        z = (z0 - travel) % 1.0 * 6 + 0.15
        focal = self.H * 0.55
        px = self.W / 2 + x / z * focal
        py = self.H / 2 + y / z * focal
        near = np.clip((6.15 - z) / 6, 0, 1)
        b = near ** 3 * (0.4 + 0.8 * f["rms"] * (0.5 + sh.intensity)) * np.clip(z * 2, 0, 1)
        img = self.splat(px, py, b * 3)
        core = ndimage.gaussian_filter(img, 0.8)
        halo = ndimage.gaussian_filter(img, 3.0) * 1.0
        lut = self.lut(sh.palette)
        bg = self.nebula(Frame(fr.t, fr.lt, {**f, "rms": f["rms"] * 0.3}, sh)) * 0.25
        glow = np.exp(-self.r ** 2 * 4.0) * (0.15 + 0.4 * f["low"] * sh.intensity)
        col = bg + (core + halo)[..., None] * lut[-1] + (halo[..., None] * lut[len(lut) * 2 // 3])
        return col + glow[..., None] * lut[-2]

    def waves(self, fr: Frame) -> np.ndarray:
        sh, f = fr.shot, fr.f
        K = 18
        lut = self.lut(sh.palette)
        img = np.zeros((self.H, self.W, 3), np.float32)
        xs = self.x[0]
        env = np.exp(-(xs / (self.aspect * 0.55)) ** 2)
        amp = 0.08 + 0.35 * (0.4 * f["energy"] + 0.6 * f["rms"]) * (0.4 + sh.intensity)
        drift = fr.t * (0.01 + 0.03 * sh.speed)
        thick = 2.2 / self.H
        for k in range(K):
            base = -0.55 + 1.35 * k / (K - 1)
            u = xs * 0.35 + drift
            n = self.sample(k % 4, u, np.full_like(u, k * 0.137 + drift * 0.3)) - 0.45
            curve = base - np.clip(n, 0, None) * 2.2 * amp * env * (0.6 + 0.4 * k / K)
            below = self.y > curve[None, :]
            img[below] *= 0.0  # occlude what is behind (above) this ridge
            line = np.exp(-((self.y - curve[None, :]) / thick) ** 2)
            c = lut[min(255, int(80 + 175 * (1 - k / K)))]
            img += line[..., None] * c * (0.45 + 0.5 * f["rms"])
        sky = np.clip(-self.y * 0.5, 0, 1) ** 2 * 0.25
        return img + sky[..., None] * lut[len(lut) // 3]

    def embers(self, fr: Frame) -> np.ndarray:
        sh, f = fr.shot, fr.f
        lut = self.lut(sh.palette)
        rise = fr.t * (0.04 + 0.1 * sh.speed)
        y = 1.0 - ((self.e_y + rise * self.e_speed) % 1.0)  # 1 bottom -> 0 top
        x = self.e_x + 0.04 * np.sin(fr.t * 0.7 * self.e_speed + self.e_y * 20)
        px = (x / 1.0 * 0.5 + 0.5) * self.W
        py = y * self.H
        life = np.sin(np.clip(y, 0, 1) * np.pi)
        b = life * self.e_size * (0.3 + 0.9 * self.e_size) * (0.6 + 0.8 * f["rms"] * (0.5 + sh.intensity))
        img = self.splat(px, py, b * 2)
        sparks = ndimage.gaussian_filter(img, (2.2, 0.7)) * 2 + ndimage.gaussian_filter(img, (7, 3)) * 2
        floor = np.clip((self.y - 0.2) * 1.2, 0, 1) ** 2 * (0.25 + 0.35 * f["energy"])
        smoke = self.smoke(Frame(fr.t, fr.lt, {**f, "rms": f["rms"] * 0.4}, sh)) * 0.07
        return smoke + sparks[..., None] * lut[-2] + floor[..., None] * lut[len(lut) // 2]

    def render(self, fr: Frame) -> np.ndarray:
        fn = getattr(self, fr.shot.scene, self.nebula)
        return fn(fr).astype(np.float32)


def post(img: np.ndarray, kit: SceneKit, f: dict, flash: float = 0.0) -> np.ndarray:
    """Bloom, flash, and a soft filmic tonemap. Returns 0..1."""
    bright = np.clip(img - 0.7, 0, None)
    small = bright[::4, ::4]
    bloom = ndimage.gaussian_filter(small, (6, 6, 0))
    bloom = np.repeat(np.repeat(bloom, 4, 0), 4, 1)[: img.shape[0], : img.shape[1]]
    img = img + bloom * 0.8 + flash
    img = img / (1 + img)  # Reinhard
    img = img * 1.6
    return np.clip(img, 0, 1) ** (1 / 2.2)
