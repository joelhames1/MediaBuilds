"""Render Claude-drawn scenes: JS canvas code -> frames (headless Chromium) -> clips/<shot>.mp4.

The page has no network access (every request is aborted) and no file access, so the code
Claude writes can only draw on its canvas.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import zlib
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from ..progress import report
from ..project import Project, load_config
from ..schemas import Analysis, Shot, Storyboard

RUNTIME = Path(__file__).resolve().parent / "runtime.html"
FEATURES = ["rms", "low", "high", "onset", "beat", "energy"]


class SceneError(RuntimeError):
    """The scene's code threw, drew nothing, or was far too slow. The message goes back to Claude."""


def art_dir(p: Project) -> Path:
    return p.path("art")


def kit_path(p: Project) -> Path:
    return art_dir(p) / "kit.js"


def scene_path(p: Project, shot_id: str) -> Path:
    return art_dir(p) / f"{shot_id}.js"


def _chromium_path() -> str | None:
    env = os.environ.get("SONGVID_CHROMIUM")
    if env:
        return env
    for cand in ["/opt/pw-browsers/chromium-1194/chrome-linux/chrome"]:  # preinstalled in some sandboxes
        if Path(cand).exists():
            return cand
    return None


@contextmanager
def browser_page(width: int, height: int):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Claude-drawn scenes need Playwright: run ./setup.sh again (it installs it).") from e
    with sync_playwright() as pw:
        kw = {"executable_path": _chromium_path()} if _chromium_path() else {}
        try:
            browser = pw.chromium.launch(**kw)
        except Exception as e:
            raise RuntimeError("Could not start the drawing browser. Run: python -m playwright install chromium") from e
        try:
            context = browser.new_context(viewport={"width": min(width, 1920), "height": min(height, 1080)},
                                          java_script_enabled=True, service_workers="block", accept_downloads=False)
            page = context.new_page()
            runtime = RUNTIME.as_uri()
            # Only the runtime page itself loads. The page's CSP also forbids fetch, WebSocket and images
            # from anywhere, so scene code can draw on its canvas and nothing else.
            context.route("**/*", lambda route: route.continue_() if route.request.url == runtime else route.abort())
            page.goto(runtime)
            yield page
        finally:
            browser.close()


def shot_info(p: Project, board: Storyboard, sh: Shot, an: Analysis, W: int, H: int, fps: int) -> dict:
    sec = next((s for s in an.sections if s.start <= sh.start + 0.01 < s.end), None)
    return {
        "id": sh.id, "W": W, "H": H, "fps": fps, "duration": round(sh.end - sh.start, 3),
        "frames": int(round((sh.end - sh.start) * fps)), "start": sh.start, "bpm": an.tempo,
        "beatSeconds": 60 / max(an.tempo, 1), "palette": sh.palette, "intensity": sh.intensity, "speed": sh.speed,
        "description": sh.description, "drawing": sh.image_prompt, "motion": sh.motion_prompt,
        "section": sh.section, "sectionEnergy": sec.energy if sec else 0.5,
        "lyricsOnScreen": sh.lyrics_overlay, "seed": zlib.crc32(sh.id.encode()) % 2_147_483_647,  # stable across runs
    }


def audio_frames(p: Project, sh: Shot, fps: int) -> dict:
    z = np.load(p.features)
    a = int(round(sh.start * fps))
    n = max(1, int(round((sh.end - sh.start) * fps)))
    out = {"n": n, "names": FEATURES}
    for k in FEATURES:
        arr = z[k] if k in z.files else np.zeros(1)
        seg = arr[a:a + n]
        if len(seg) < n:
            seg = np.pad(seg, (0, n - len(seg)), mode="edge") if len(seg) else np.zeros(n)
        out[k] = [round(float(x), 4) for x in seg]
    return out


def _load(page, p: Project, board: Storyboard, sh: Shot, an: Analysis, W: int, H: int, fps: int, code: str | None = None):
    kit = kit_path(p).read_text() if kit_path(p).exists() else ""
    code = code if code is not None else scene_path(p, sh.id).read_text()
    info = shot_info(p, board, sh, an, W, H, fps)
    try:
        page.evaluate("([k, s, i, a]) => __load(k, s, i, a)", [kit, code, info, audio_frames(p, sh, fps)])
    except Exception as e:
        raise SceneError(f"Setting up the scene failed: {_clean(e)}") from None
    return info


def _clean(e: Exception) -> str:
    msg = str(e).split("\n")[0]
    return msg.replace("Error: Page.evaluate: ", "").replace("Page.evaluate: ", "")[:500]


def check_and_still(p: Project, sh: Shot, code: str | None = None) -> dict:
    """Load the scene, probe a few frames for errors / blank output / slowness, save its still."""
    cfg = load_config(p)
    board, an = p.read(p.storyboard, Storyboard), p.read(p.analysis, Analysis)
    W, H, fps = cfg["art"]["width"], cfg["art"]["height"], cfg["video"]["fps"]
    with browser_page(W, H) as page:
        info = _load(page, p, board, sh, an, W, H, fps, code)
        n = info["frames"]
        stats = []
        for i in sorted({0, n // 3, (2 * n) // 3, max(0, n - 1)}):
            try:
                stats.append(page.evaluate("i => __probe(i)", i))
            except Exception as e:
                raise SceneError(f"draw() threw at t={i / fps:.2f}s: {_clean(e)}") from None
        if max(s["range"] for s in stats) < 3:
            raise SceneError("Every probed frame is a single flat colour; nothing visible is being drawn.")
        slow = max(s["ms"] for s in stats)
        if slow > cfg["art"]["max_frame_ms"]:
            raise SceneError(f"draw() takes {slow:.0f} ms per frame at {W}x{H}; keep it under "
                             f"{cfg['art']['max_frame_ms']} ms (fewer particles/strokes per frame, or cache static layers).")
        # still: reload so stateful scenes start clean, then render up to 40% of the way in
        _load(page, p, board, sh, an, W, H, fps, code)
        target = int(n * 0.4)
        url = None
        for start in range(0, target + 1, 24):
            urls = page.evaluate("([s, c]) => __frames(s, c, 0.92)", [start, min(24, target + 1 - start)])
            url = urls[-1] if urls else url
    dest = p.stills_dir / f"{sh.id}_key.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _jpeg_to(dest, url)
    return {"ms_per_frame": round(slow, 1)}


def _jpeg_to(dest: Path, data_url: str) -> None:
    from io import BytesIO

    from PIL import Image
    raw = base64.b64decode(data_url.split(",", 1)[1])
    Image.open(BytesIO(raw)).convert("RGB").save(dest)


def render_clips(p: Project, shot_ids: list[str]) -> list[Path]:
    """Render full clips for the given Claude-drawn shots, one after another, with progress."""
    cfg = load_config(p)
    board, an = p.read(p.storyboard, Storyboard), p.read(p.analysis, Analysis)
    W, H, fps = cfg["art"]["width"], cfg["art"]["height"], cfg["video"]["fps"]
    shots = [s for s in board.shots if s.id in shot_ids and scene_path(p, s.id).exists()]
    total = sum(int(round((s.end - s.start) * fps)) for s in shots) or 1
    done, out = 0, []
    p.clips_dir.mkdir(parents=True, exist_ok=True)
    with browser_page(W, H) as page:
        for k, sh in enumerate(shots, 1):
            info = _load(page, p, board, sh, an, W, H, fps)
            n = info["frames"]
            dest = p.clips_dir / f"{sh.id}.mp4"
            tmp = dest.with_name(f".{sh.id}.tmp.mp4")
            enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "image2pipe", "-c:v", "mjpeg", "-r", str(fps),
                                    "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                                    str(tmp)], stdin=subprocess.PIPE)
            try:
                for start in range(0, n, 12):
                    try:
                        urls = page.evaluate("([s, c]) => __frames(s, c, 0.93)", [start, min(12, n - start)])
                    except Exception as e:
                        raise SceneError(f"{sh.id}: draw() threw at t={start / fps:.2f}s: {_clean(e)}") from None
                    for u in urls:
                        enc.stdin.write(base64.b64decode(u.split(",", 1)[1]))
                    done += len(urls)
                    report(done / total, f"scene {k}/{len(shots)} ({sh.id}) · frame {start + len(urls)}/{n}")
            finally:
                enc.stdin.close()
                enc.wait()
            if enc.returncode:
                raise RuntimeError(f"ffmpeg failed encoding {sh.id}")
            os.replace(tmp, dest)
            out.append(dest)
            print(f"  rendered {sh.id} ({n} frames)", file=sys.stderr)
    return out
