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


def keyframes(p: Project, only: list[str] | None = None, redo: bool = False) -> list[Path]:
    cfg = load_config(p)
    board = p.read(p.storyboard, Storyboard)
    fal = Fal(cfg)
    g = cfg["generate"]
    out = []
    for sh in _gen_shots(board, only):
        dest = p.stills_dir / f"{sh.id}_key.png"
        if dest.exists() and not redo:
            out.append(dest)
            continue
        note = _approvals(p).get(sh.id, {}).get("note", "")
        prompt = ", ".join(x for x in [board.look, sh.image_prompt, note] if x)
        print(f"  keyframe {sh.id}: {prompt[:90]}...", file=sys.stderr)
        res = fal.run(g["image_model"], {"prompt": prompt, **g["image_args"]})
        out.append(fal.download(res["images"][0]["url"], dest))
        approve(p, [sh.id], value=False)  # new image -> needs a fresh look
    return out


def plan_animate(p: Project, only: list[str] | None = None) -> list[tuple[Shot, int]]:
    cfg = load_config(p)
    board = p.read(p.storyboard, Storyboard)
    ap = _approvals(p)
    durs = sorted(cfg["generate"]["video_durations"])
    plan = []
    for sh in _gen_shots(board, only):
        if not ap.get(sh.id, {}).get("approved"):
            continue
        if (p.clips_dir / f"{sh.id}.mp4").exists():
            continue
        need = sh.end - sh.start
        plan.append((sh, next((d for d in durs if d >= need), durs[-1])))
    return plan


def animate(p: Project, only: list[str] | None = None) -> list[Path]:
    cfg = load_config(p)
    g = cfg["generate"]
    fal = Fal(cfg)
    out = []
    for sh, dur in plan_animate(p, only):
        key = p.stills_dir / f"{sh.id}_key.png"
        if not key.exists():
            print(f"  {sh.id}: no keyframe, skipping", file=sys.stderr)
            continue
        uri = "data:image/png;base64," + base64.b64encode(key.read_bytes()).decode()
        args = {"prompt": sh.motion_prompt or sh.image_prompt, "duration": str(dur),
                g["video_image_field"]: uri, **g["video_args"]}
        print(f"  animating {sh.id} ({dur}s)...", file=sys.stderr)
        res = fal.run(g["video_model"], args)
        url = (res.get("video") or {}).get("url") or res["videos"][0]["url"]
        out.append(fal.download(url, p.clips_dir / f"{sh.id}.mp4"))
    return out
