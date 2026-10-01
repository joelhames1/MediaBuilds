"""Frame loop: shot sources -> transitions -> grade -> lyrics -> ffmpeg.

Per shot, the picture comes from (first available):
  generated clip     clips/<shot>.mp4      (from `songvid animate`)
  generated still    stills/<shot>_key.png (from `songvid keyframes`), with a slow push-in
  procedural scene   scenes.py
"""

from __future__ import annotations

import bisect
import math
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..progress import report
from ..project import Project, load_config
from ..schemas import Analysis, Shot, Storyboard, Timing
from .scenes import Frame, SceneKit, post
from .typography import LyricLayer, find_font

FADE_T = 0.6
LETTERBOX = 2.39


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


class ClipReader:
    """Sequential frames from a generated clip, fitted to the shot length."""

    def __init__(self, path: Path, shot_dur: float, first_rel: int, W: int, H: int, fps: int):
        self.W, self.H = W, H
        clip_dur = _ffprobe_duration(path) or shot_dur
        ratio = shot_dur / clip_dur
        slow = min(1.6, max(1.0, ratio))  # stretch a little, loop if still short
        loop = ratio > 1.6
        vf = (f"setpts={slow:.4f}*PTS,fps={fps},"
              f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}")
        cmd = ["ffmpeg", "-v", "error"] + (["-stream_loop", "-1"] if loop else []) + [
            "-i", str(path), "-vf", vf, "-ss", f"{first_rel / fps:.4f}", "-an",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.pos = first_rel - 1
        self.last = np.zeros((H, W, 3), np.uint8)

    def get(self, rel: int) -> np.ndarray:
        n = self.W * self.H * 3
        while self.pos < rel:
            buf = self.proc.stdout.read(n)
            if len(buf) < n:
                self.pos = rel  # EOF: hold last frame
                break
            self.last = np.frombuffer(buf, np.uint8).reshape(self.H, self.W, 3)
            self.pos += 1
        return self.last

    def close(self):
        self.proc.kill()


class Renderer:
    def __init__(self, p: Project, cfg: dict, preview: bool = False):
        self.p, self.cfg = p, cfg
        v = cfg["video"]
        self.W, self.H = (960, 540) if preview else (v["width"], v["height"])
        self.fps = v["fps"]
        s = v["render_scale"] if not preview else 0.67
        self.kit = SceneKit(int(self.W * s) // 2 * 2, int(self.H * s) // 2 * 2)
        self.board = p.read(p.storyboard, Storyboard)
        self.an = p.read(p.analysis, Analysis)
        self.shots = self.board.shots
        self.starts = [sh.start for sh in self.shots]
        z = np.load(p.features)
        self.feats = {k: z[k] for k in z.files if k != "fps"}
        self.n = len(self.feats["rms"])
        self.timing = p.read(p.timing, Timing) if p.timing.exists() else None
        font = self.board.font or v.get("font")
        self.bars = 0
        if self.board.letterbox:
            self.bars = int(round((self.H - self.W / LETTERBOX) / 2))
        bottom = 1 - (self.bars / self.H) - 0.035
        self.lyrics = LyricLayer(self.timing, self.W, self.H, font, bottom=bottom) if self.timing else None
        # lyric line -> overlay allowed by the shot it starts in
        self.line_ok = {}
        if self.timing:
            for ln in self.timing.lines:
                self.line_ok[ln.index] = self.shot_at(ln.start).lyrics_overlay
        ys, xs = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
        r = np.sqrt(((xs / self.W - 0.5) * 1.6) ** 2 + ((ys / self.H - 0.5) * 1.2) ** 2)
        self.vignette = (1 - 0.45 * np.clip(r - 0.35, 0, 1) ** 1.5)[..., None].astype(np.float32)
        rng = np.random.default_rng(3)
        self.grain = [rng.normal(0, 0.018, (self.H // 2, self.W // 2)).astype(np.float32) for _ in range(6)]
        self.readers: dict[str, ClipReader] = {}
        self.stills: dict[str, Image.Image] = {}

    # ---------- lookup ----------

    def shot_at(self, t: float) -> Shot:
        k = max(0, bisect.bisect_right(self.starts, t) - 1)
        return self.shots[k]

    def f_at(self, i: int) -> dict:
        i = min(max(i, 0), self.n - 1)
        return {k: float(v[i]) for k, v in self.feats.items()}

    def clip_path(self, sh: Shot) -> Path | None:
        c = self.p.clips_dir / f"{sh.id}.mp4"
        return c if sh.source == "generated" and c.exists() else None

    def still_path(self, sh: Shot) -> Path | None:
        c = self.p.stills_dir / f"{sh.id}_key.png"
        return c if sh.source == "generated" and c.exists() else None

    # ---------- sources ----------

    def _procedural(self, sh: Shot, t: float, f: dict) -> np.ndarray:
        return self.kit.render(Frame(t, t - sh.start, f, sh))

    def _to_out(self, img01: np.ndarray, zoom: float) -> np.ndarray:
        """Kit-res 0..1 float -> output-res float, with a centered zoom."""
        im = Image.fromarray((np.clip(img01, 0, 1) * 255).astype(np.uint8))
        return self._resize(im, zoom)

    def _resize(self, im: Image.Image, zoom: float) -> np.ndarray:
        w, h = im.size
        cw, ch = w / zoom, h / zoom
        box = ((w - cw) / 2, (h - ch) / 2, (w + cw) / 2, (h + ch) / 2)
        return np.asarray(im.resize((self.W, self.H), Image.BICUBIC, box=box), np.float32) / 255

    def _still(self, sh: Shot, path: Path, lt: float, zoom: float) -> np.ndarray:
        if sh.id not in self.stills:
            im = Image.open(path).convert("RGB")
            # cover-fit to output aspect
            s = max(self.W / im.width, self.H / im.height)
            im = im.resize((math.ceil(im.width * s), math.ceil(im.height * s)), Image.LANCZOS)
            l, t = (im.width - self.W) // 2, (im.height - self.H) // 2
            self.stills[sh.id] = im.crop((l, t, l + self.W, t + self.H))
        dur = max(0.1, sh.end - sh.start)
        push = 1.0 + 0.10 * (lt / dur) * (0.5 + sh.speed)
        return self._resize(self.stills[sh.id], zoom * push)

    def _clip(self, sh: Shot, path: Path, i: int, zoom: float) -> np.ndarray:
        rel = i - int(round(sh.start * self.fps))
        rd = self.readers.get(sh.id)
        if rd is None or rd.pos > rel:
            if rd:
                rd.close()
            rd = self.readers[sh.id] = ClipReader(path, sh.end - sh.start, rel, self.W, self.H, self.fps)
        arr = rd.get(rel)
        if zoom == 1.0:
            return arr.astype(np.float32) / 255
        return self._resize(Image.fromarray(arr), zoom)

    def picture(self, sh: Shot, i: int, t: float, f: dict, zoom: float, flash: float) -> np.ndarray:
        clip, still = self.clip_path(sh), self.still_path(sh)
        if clip:
            return np.clip(self._clip(sh, clip, i, zoom) + flash * 0.6, 0, 1)
        if still:
            return np.clip(self._still(sh, still, t - sh.start, zoom) + flash * 0.6, 0, 1)
        return self._to_out(post(self._procedural(sh, t, f), self.kit, f, flash), zoom)

    # ---------- frame ----------

    def frame(self, i: int, overlays: bool = True) -> np.ndarray:
        t = i / self.fps
        f = self.f_at(i)
        k = max(0, bisect.bisect_right(self.starts, t) - 1)
        sh = self.shots[k]
        lt = t - sh.start
        zoom = 1.0 + 0.05 * sh.punch * f["beat"]
        flash = 0.7 * math.exp(-lt / 0.09) if sh.transition_in == "flash" else 0.0

        img = self.picture(sh, i, t, f, zoom, flash)
        if sh.transition_in == "fade" and k > 0 and lt < FADE_T:
            a = lt / FADE_T
            a = a * a * (3 - 2 * a)
            prev = self.shots[k - 1]
            if not (self.clip_path(prev) or self.still_path(prev) or self.clip_path(sh) or self.still_path(sh)):
                img = img * a + self.picture(prev, i, t, f, zoom, 0.0) * (1 - a)  # crossfade
            else:
                img = img * a  # dip from black
        # fade out the very end of the song
        tail = self.an.duration - t
        if tail < 1.5:
            img = img * max(0.0, tail / 1.5)

        img = img * self.vignette
        g = self.grain[i % len(self.grain)]
        img += np.repeat(np.repeat(g, 2, 0), 2, 1)[: self.H, : self.W, None]
        if self.lyrics and overlays:
            self.lyrics.draw(img, t, allowed=lambda li: self.line_ok.get(li, True))
        if self.bars and overlays:
            img[: self.bars] = 0
            img[self.H - self.bars:] = 0
        return (np.clip(img, 0, 1) * 255).astype(np.uint8)

    def close(self):
        for rd in self.readers.values():
            rd.close()


# ---------- encoding ----------

def _render_chunk(args) -> str:
    slug, preview, a, b, out, cfg = args
    r = Renderer(Project(slug), cfg, preview)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{r.W}x{r.H}",
           "-r", str(r.fps), "-i", "-", "-c:v", "libx264", "-preset", "medium",
           "-crf", str(cfg["video"]["crf"]), "-pix_fmt", "yuv420p", out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    prog = Path(out).with_suffix(".prog")
    for i in range(a, b):
        fr = r.frame(i)
        enc.stdin.write(fr.tobytes())
        if (i - a) % 24 == 0 and i > a:
            prog.write_text(str(i - a))
            if a == 0 and (i - a) % 48 == 0:
                rate = (i - a) / (time.time() - t0)
                print(f"  worker 0: {i - a}/{b - a} frames, {rate:.1f} fps", file=sys.stderr)
                # last rendered frame, for the UI's job card
                Image.fromarray(fr).resize((384, 216)).save(Path(out).parent.parent / ".live.jpg", quality=75)
    prog.write_text(str(b - a))
    enc.stdin.close()
    enc.wait()
    r.close()
    if enc.returncode:
        raise RuntimeError(f"ffmpeg failed on chunk {a}-{b}")
    return out


def render(p: Project, preview: bool = False, start: float = 0.0, end: float | None = None,
           name: str | None = None, workers: int | None = None) -> Path:
    cfg = load_config(p)
    an = p.read(p.analysis, Analysis)
    fps = cfg["video"]["fps"]
    end = min(end or an.duration, an.duration)
    a, b = int(start * fps), int(end * fps)
    workers = max(1, min(workers or cfg["video"]["workers"], (b - a) // fps or 1))
    bounds = np.linspace(a, b, workers + 1).astype(int)
    p.renders_dir.mkdir(parents=True, exist_ok=True)
    name = name or ("preview" if preview else "final") + (f"_{start:.0f}-{end:.0f}" if start or end < an.duration else "")
    out = p.renders_dir / f"{name}.mp4"
    print(f"  rendering {b - a} frames ({end - start:.1f} s) with {workers} worker(s)...", file=sys.stderr)
    t0 = time.time()
    with tempfile.TemporaryDirectory(dir=p.renders_dir) as tmp:
        jobs = [(p.slug, preview, int(bounds[k]), int(bounds[k + 1]), f"{tmp}/part{k:03d}.mp4", cfg)
                for k in range(workers) if bounds[k + 1] > bounds[k]]
        stop = threading.Event()

        def watch():
            while not stop.wait(0.5):
                done = 0
                for f in Path(tmp).glob("*.prog"):
                    try:
                        done += int(f.read_text() or 0)
                    except ValueError:
                        pass
                el = time.time() - t0
                eta = el / done * (b - a - done) if done else None
                report(done / max(1, b - a), f"{done}/{b - a} frames" + (f", {eta:.0f} s left" if eta else ""))

        threading.Thread(target=watch, daemon=True).start()
        try:
            if len(jobs) == 1:
                parts = [_render_chunk(jobs[0])]
            else:
                with ProcessPoolExecutor(len(jobs)) as ex:
                    parts = list(ex.map(_render_chunk, jobs))
        finally:
            stop.set()
        report(1.0, "muxing audio")
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{Path(x).resolve()}'\n" for x in parts))
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
               "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(p.audio()),
               "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "320k",
               "-shortest", "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True)
    print(f"  done in {time.time() - t0:.0f} s -> {out}", file=sys.stderr)
    return out


# ---------- stills / contact sheet ----------

def stills(p: Project, preview: bool = True, at: float = 0.4) -> Path:
    """One frame per shot (at 40% through it) plus a labelled contact sheet."""
    cfg = load_config(p)
    r = Renderer(p, cfg, preview)
    p.stills_dir.mkdir(parents=True, exist_ok=True)
    thumbs = []
    for n, sh in enumerate(r.shots):
        report(n / len(r.shots), f"still {n + 1}/{len(r.shots)}")
        i = int((sh.start + (sh.end - sh.start) * at) * r.fps)
        im = Image.fromarray(r.frame(i))
        im.save(p.stills_dir / f"{sh.id}.jpg", quality=90)
        # clean thumbnail (no lyrics, no letterbox) for the UI timeline
        Image.fromarray(r.frame(i, overlays=False)).resize((960, 540), Image.LANCZOS).save(
            p.stills_dir / f"{sh.id}_thumb.jpg", quality=82)
        thumbs.append((sh, im))
    r.close()
    cols = 4
    tw, th = 480, 270
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * tw, rows * (th + 46)), (14, 14, 16))
    fp = find_font(None)
    font = ImageFont.truetype(fp, 15) if fp else ImageFont.load_default()
    d = ImageDraw.Draw(sheet)
    for n, (sh, im) in enumerate(thumbs):
        x, y = (n % cols) * tw, (n // cols) * (th + 46)
        sheet.paste(im.resize((tw, th)), (x, y))
        src = "gen" if sh.source == "generated" else sh.scene
        d.text((x + 8, y + th + 4), f"{sh.id}  {sh.start:6.1f}-{sh.end:5.1f}s  {sh.section}  [{src}]",
               fill=(230, 225, 215), font=font)
        d.text((x + 8, y + th + 24), sh.description[:62], fill=(150, 150, 150), font=font)
    out = p.stills_dir / "contact_sheet.jpg"
    sheet.save(out, quality=88)
    return out
