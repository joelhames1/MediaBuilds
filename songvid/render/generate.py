"""Third-party generation via fal.ai's queue API: keyframe stills, then image-to-video.

One FAL_KEY reaches many models (Flux, Kling, Veo, Seedance, ...). Model ids and request
fields live in config (`generate:`) because they change faster than this code.

Flow: keyframes (cheap) -> you approve stills -> animate (expensive) only approved shots.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

from ..project import Project, load_config
from ..schemas import Shot, Storyboard

QUEUE = "https://queue.fal.run"


class Fal:
    def __init__(self, cfg: dict):
        key = os.environ.get("FAL_KEY")
        if not key:
            raise RuntimeError("Set FAL_KEY to generate images/video (https://fal.ai/dashboard/keys).")
        self.g = cfg["generate"]
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Key {key}", "Content-Type": "application/json"})

    def run(self, model: str, args: dict) -> dict:
        r = self.s.post(f"{QUEUE}/{model}", json=args, timeout=120)
        r.raise_for_status()
        job = r.json()
        status_url, response_url = job["status_url"], job["response_url"]
        deadline = time.time() + self.g["timeout_seconds"]
        while time.time() < deadline:
            st = self.s.get(status_url, timeout=60).json()
            if st.get("status") == "COMPLETED":
                res = self.s.get(response_url, timeout=120)
                res.raise_for_status()
                return res.json()
            if st.get("status") in ("FAILED", "ERROR"):
                raise RuntimeError(f"fal job failed: {json.dumps(st)[:400]}")
            time.sleep(self.g["poll_seconds"])
        raise TimeoutError(f"fal job timed out: {model}")

    def download(self, url: str, dest: Path) -> Path:
        r = requests.get(url, timeout=600)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest


def _approvals(p: Project) -> dict:
    f = p.path("approvals.json")
    return json.loads(f.read_text()) if f.exists() else {}


def approve(p: Project, ids: list[str], value: bool = True, note: str = "") -> dict:
    ap = _approvals(p)
    board = p.read(p.storyboard, Storyboard)
    targets = [s.id for s in board.shots if s.source == "generated"] if ids == ["all"] else ids
    for i in targets:
        ap[i] = {"approved": value, "note": note}
    p.write(p.path("approvals.json"), ap)
    return ap


def _gen_shots(board: Storyboard, only: list[str] | None) -> list[Shot]:
    return [s for s in board.shots if s.source == "generated" and (not only or s.id in only)]


def _parallel(items, work, label: str, workers: int):
    """Run `work(item)` for each item, a few at a time, reporting as each one lands."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from ..progress import report

    results, errors, n = [], [], len(items)
    if not n:
        return results
    report(0.0, f"0/{n} {label} · {min(workers, n)} generating at fal.ai")
    with ThreadPoolExecutor(max(1, min(workers, n))) as ex:
        futs = {ex.submit(work, it): it for it in items}
        for k, f in enumerate(as_completed(futs), 1):
            try:
                results.append(f.result())
                last = getattr(futs[f], "id", None) or getattr(futs[f][0], "id", "")
                running = n - k
                report(k / n, f"{k}/{n} {label} · {last} done" + (f" · {min(workers, running)} generating" if running else ""))
            except Exception as e:  # keep going; report the failures at the end
                errors.append(f"{e}")
                report(k / n, f"{k}/{n} {label} · one failed: {str(e)[:80]}")
    if errors:
        raise RuntimeError(f"{len(errors)} of {n} {label} failed. First error: {errors[0]}")
    return results


def keyframes(p: Project, only: list[str] | None = None, redo: bool = False) -> list[Path]:
    cfg = load_config(p)
    board = p.read(p.storyboard, Storyboard)
    fal = Fal(cfg)
    g = cfg["generate"]
    notes = _approvals(p)
    todo = [sh for sh in _gen_shots(board, only) if redo or not (p.stills_dir / f"{sh.id}_key.png").exists()]

    def one(sh: Shot) -> Path:
        prompt = ", ".join(x for x in [board.look, sh.image_prompt, notes.get(sh.id, {}).get("note", "")] if x)
        print(f"  keyframe {sh.id}: {prompt[:90]}...", file=sys.stderr)
        res = fal.run(g["image_model"], {"prompt": prompt, **g["image_args"]})
        return fal.download(res["images"][0]["url"], p.stills_dir / f"{sh.id}_key.png")

    try:
        return _parallel(todo, one, "stills", g.get("concurrency", 4))
    finally:
        # New images need a fresh look; written once, after the threads, so they can't race.
        made = [sh.id for sh in todo if (p.stills_dir / f"{sh.id}_key.png").exists()]
        if made:
            approve(p, made, value=False)


def clip_seconds(need: float, durations: list[int], max_stretch: float = 1.3) -> int:
    """Shortest clip that covers the shot when slowed down at most `max_stretch` times."""
    durs = sorted(durations)
    return next((d for d in durs if d * max_stretch >= need), durs[-1])


def plan_animate(p: Project, only: list[str] | None = None) -> list[tuple[Shot, int]]:
    cfg = load_config(p)
    board = p.read(p.storyboard, Storyboard)
    ap = _approvals(p)
    g = cfg["generate"]
    plan = []
    for sh in _gen_shots(board, only):
        if not ap.get(sh.id, {}).get("approved"):
            continue
        if (p.clips_dir / f"{sh.id}.mp4").exists():
            continue
        plan.append((sh, clip_seconds(sh.end - sh.start, g["video_durations"], g.get("max_stretch", 1.3))))
    return plan


def animate(p: Project, only: list[str] | None = None) -> list[Path]:
    cfg = load_config(p)
    g = cfg["generate"]
    fal = Fal(cfg)
    plan = [(sh, d) for sh, d in plan_animate(p, only) if (p.stills_dir / f"{sh.id}_key.png").exists()]

    def one(item) -> Path:
        sh, dur = item
        key = p.stills_dir / f"{sh.id}_key.png"
        uri = "data:image/png;base64," + base64.b64encode(key.read_bytes()).decode()
        args = {"prompt": sh.motion_prompt or sh.image_prompt, "duration": str(dur),
                g["video_image_field"]: uri, **g["video_args"]}
        print(f"  animating {sh.id} ({dur}s)...", file=sys.stderr)
        res = fal.run(g["video_model"], args)
        url = (res.get("video") or {}).get("url") or res["videos"][0]["url"]
        return fal.download(url, p.clips_dir / f"{sh.id}.mp4")

    return _parallel(plan, one, "clips", g.get("concurrency", 4))
