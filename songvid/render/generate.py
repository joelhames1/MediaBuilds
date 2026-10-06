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
    targets = [s.id for s in board.shots if s.source in ("generated", "claude")] if ids == ["all"] else ids
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
    g = cfg["generate"]
    use_gemini = g.get("image_provider") == "gemini"
    if use_gemini:
        from .gemini import Gemini
        gem = Gemini(g["gemini_image_model"], g.get("gemini_api", "interactions"))
    else:
        fal = Fal(cfg)
    notes = _approvals(p)
    todo = [sh for sh in _gen_shots(board, only) if redo or not (p.stills_dir / f"{sh.id}_key.png").exists()]

    def one(sh: Shot) -> Path:
        prompt = ", ".join(x for x in [board.look, sh.image_prompt, notes.get(sh.id, {}).get("note", "")] if x)
        print(f"  keyframe {sh.id}: {prompt[:90]}...", file=sys.stderr)
        dest = p.stills_dir / f"{sh.id}_key.png"
        if use_gemini:
            refs = [p.path(r) for r in sh.refs]
            missing = [str(r) for r in refs if not r.exists()]
            if missing:
                raise RuntimeError(f"{sh.id}: reference image(s) not found: {', '.join(missing)}")
            img = gem.image(prompt, refs, size=g.get("gemini_image_size", "2K"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            from io import BytesIO

            from PIL import Image
            Image.open(BytesIO(img)).convert("RGB").save(dest)
            return dest
        res = fal.run(g["image_model"], {"prompt": prompt, **g["image_args"]})
        return fal.download(res["images"][0]["url"], dest)

    try:
        return _parallel(todo, one, "stills", g.get("concurrency", 4))
    finally:
        # New images need a fresh look; written once, after the threads, so they can't race.
        made = [sh.id for sh in todo if (p.stills_dir / f"{sh.id}_key.png").exists()]
        if made:
            approve(p, made, value=False)


# ---------- video models and spend ----------

def video_profile(g: dict, name: str = "") -> dict:
    """Resolve a shot's video model: a name from `video_models`, a raw fal id, or the legacy default."""
    models = g.get("video_models") or {}
    name = name or g.get("video_default") or ""
    if name in models:
        prof = {"name": name, "duration_format": "{d}", "args": {}, "usd_per_second": None, **models[name]}
        return prof
    legacy = {"image_field": g["video_image_field"], "durations": g["video_durations"], "duration_format": "{d}",
              "args": g.get("video_args", {}), "usd_per_second": g.get("video_usd_per_second")}
    if not name:
        return {"name": "default", "model": g["video_model"], **legacy}
    if "/" in name:  # a raw fal model id; we don't know its price
        return {"name": name, "model": name, **{**legacy, "usd_per_second": None}}
    raise ValueError(f"Unknown video model '{name}'. Known: {', '.join(models) or 'none'}.")


_spend_lock = __import__("threading").Lock()


def spend_log(p: Project) -> list[dict]:
    f = p.path("spend.json")
    return json.loads(f.read_text()).get("entries", []) if f.exists() else []


def spent_usd(p: Project) -> float:
    return round(sum(e.get("usd") or 0 for e in spend_log(p)), 4)


def record_spend(p: Project, entry: dict) -> None:
    with _spend_lock:
        entries = spend_log(p) + [{"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}]
        p.write(p.path("spend.json"), {"entries": entries, "total_usd": round(sum(e.get("usd") or 0 for e in entries), 4)})


def check_cap(p: Project, g: dict, planned_usd: float, unknown_price: bool = False) -> None:
    """Refuse before submitting anything that could cross the project's spend cap."""
    cap = g.get("spend_cap_usd")
    if cap is None:
        return
    if unknown_price:
        raise RuntimeError("A spend cap is set but one of these models has no usd_per_second in config, "
                           "so the cap can't be enforced. Add its price under generate.video_models.")
    used = spent_usd(p)
    if used + planned_usd > cap + 1e-9:
        raise RuntimeError(f"Over the spend cap: this would cost about ${planned_usd:.2f}, "
                           f"${used:.2f} of the ${cap:.2f} cap is already spent.")


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
        prof = video_profile(g, sh.video_model)
        plan.append((sh, clip_seconds(sh.end - sh.start, prof["durations"], g.get("max_stretch", 1.3))))
    return plan


def plan_cost(p: Project, plan: list[tuple[Shot, int]]) -> tuple[float, bool]:
    """Estimated dollars for a plan, and whether any model's price is unknown."""
    g = load_config(p)["generate"]
    total, unknown = 0.0, False
    for sh, d in plan:
        rate = video_profile(g, sh.video_model).get("usd_per_second")
        if rate is None:
            unknown = True
        else:
            total += rate * d
    return round(total, 4), unknown


def animate(p: Project, only: list[str] | None = None) -> list[Path]:
    cfg = load_config(p)
    g = cfg["generate"]
    fal = Fal(cfg)
    plan = [(sh, d) for sh, d in plan_animate(p, only) if (p.stills_dir / f"{sh.id}_key.png").exists()]
    check_cap(p, g, *plan_cost(p, plan))

    def one(item) -> Path:
        sh, dur = item
        prof = video_profile(g, sh.video_model)
        key = p.stills_dir / f"{sh.id}_key.png"
        uri = "data:image/png;base64," + base64.b64encode(key.read_bytes()).decode()
        args = {"prompt": sh.motion_prompt or sh.image_prompt, "duration": prof["duration_format"].format(d=dur),
                prof["image_field"]: uri, **prof["args"]}
        print(f"  animating {sh.id} ({dur}s on {prof['name']})...", file=sys.stderr)
        res = fal.run(prof["model"], args)
        rate = prof.get("usd_per_second")
        record_spend(p, {"shot": sh.id, "model": prof["model"], "seconds": dur,
                         "usd": round(rate * dur, 4) if rate is not None else None})
        url = (res.get("video") or {}).get("url") or res["videos"][0]["url"]
        return fal.download(url, p.clips_dir / f"{sh.id}.mp4")

    return _parallel(plan, one, "clips", g.get("concurrency", 4))
