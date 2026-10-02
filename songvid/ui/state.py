"""Project state for the UI: what exists, what's stale, what needs the user.

Staleness works like a build system. When a stage finishes, we record a signature of
its inputs in .songvid_state.json; if the inputs change later, the stage is stale.
Outputs made by the CLI (no record yet) are adopted as fresh the first time we see them.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import numpy as np

from ..lyrics import parse
from ..project import Project
from ..schemas import Analysis, SongSpec, Storyboard, SunoResult, Timing

STAGES = ["song", "suno", "timing", "board", "look", "picture", "render"]


def _mt(path: Path) -> str:
    return f"{path.stat().st_mtime_ns}:{path.stat().st_size}" if path.exists() else "-"


def _audio_sig(p: Project) -> str:
    try:
        return _mt(p.audio())
    except FileNotFoundError:
        return "-"


def _lyrics_sig(p: Project) -> str:
    if not p.song.exists():
        return "-"
    spec = p.read(p.song, SongSpec)
    sung = "\n".join(f"{ln.section}|{ln.text}" for ln in parse(spec.lyrics))
    return hashlib.sha1(sung.encode()).hexdigest()[:12]


def signature(p: Project, stage: str) -> str:
    if stage == "timing":
        return "|".join([_lyrics_sig(p), _audio_sig(p), _mt(p.suno_aligned), _mt(p.suno_lrc), _mt(p.suno_srt)])
    if stage == "analysis":
        return "|".join([_audio_sig(p), _mt(p.timing)])
    if stage == "board":
        return _mt(p.analysis)
    if stage == "look":
        return "|".join([_mt(p.storyboard), _mt(p.timing)])
    return ""


_lock = threading.Lock()


def _records(p: Project) -> dict:
    f = p.path(".songvid_state.json")
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def record(p: Project, *stages: str) -> None:
    with _lock:  # job threads and request handlers both record; write atomically
        rec = _records(p)
        for st in stages:
            rec[st] = signature(p, st)
        tmp = p.path(".songvid_state.json.tmp")
        tmp.write_text(json.dumps(rec, indent=1))
        os.replace(tmp, p.path(".songvid_state.json"))


def _fresh(p: Project, stage: str, output: Path) -> bool:
    rec = _records(p)
    if stage not in rec:
        if output.exists():
            record(p, stage)
        return True
    return rec[stage] == signature(p, stage)


def approvals(p: Project) -> dict:
    f = p.path("approvals.json")
    return json.loads(f.read_text()) if f.exists() else {}


def statuses(p: Project, running: set[str]) -> dict:
    st: dict[str, dict] = {}

    def put(stage, state, text):
        st[stage] = {"state": "running" if stage in running else state, "text": text}

    put("song", "done" if p.song.exists() else "empty",
        "Lyrics, style and settings for Suno." if p.song.exists() else "Write the song first.")
    try:
        audio = p.audio().name
    except FileNotFoundError:
        audio = None
    put("suno", "done" if audio else ("needs" if p.song.exists() else "empty"),
        f"Using {audio}." if audio else "Make it in Suno, then drop the MP3 here.")

    if not p.timing.exists():
        put("timing", "empty", "Align the lyrics to the audio.")
    elif not _fresh(p, "timing", p.timing):
        put("timing", "stale", "Lyrics or audio changed since the last alignment.")
    else:
        tm = p.read(p.timing, Timing)
        low = sum(not w.confident for w in tm.words)
        put("timing", "done", f"{len(tm.words)} words timed via {tm.source}." + (f" {low} interpolated words to check." if low else ""))

    board_ok = p.storyboard.exists()
    if p.analysis.exists() and not _fresh(p, "analysis", p.analysis):
        put("board", "stale", "Timing changed; the analysis and shot list need a refresh.")
    elif not board_ok:
        put("board", "empty" if not p.analysis.exists() else "needs", "Generate a storyboard.")
    elif not _fresh(p, "board", p.storyboard):
        put("board", "stale", "Analysis changed; shots need re-fitting to the new bar lines.")
    else:
        b = p.read(p.storyboard, Storyboard)
        g = sum(s.source == "generated" for s in b.shots)
        put("board", "done", f"{len(b.shots)} shots on bar lines" + (f", {g} with AI imagery." if g else "."))

    if not board_ok:
        put("look", "empty", "Stills appear once there is a storyboard.")
        put("picture", "empty", "")
        put("render", "empty", "")
        return st
    board = p.read(p.storyboard, Storyboard)
    ap = approvals(p)
    gen = [s for s in board.shots if s.source == "generated"]
    have_stills = all((p.stills_dir / f"{s.id}_thumb.jpg").exists() for s in board.shots)
    if not have_stills:
        put("look", "stale" if p.stills_dir.exists() else "empty", "Render stills to review the look.")
    elif not _fresh(p, "look", p.stills_dir / "contact_sheet.jpg"):
        put("look", "stale", "The storyboard changed; stills are out of date.")
    else:
        no_key = [s.id for s in gen if not (p.stills_dir / f"{s.id}_key.png").exists()]
        waiting = [s.id for s in gen if not ap.get(s.id, {}).get("approved") and s.id not in no_key]
        if no_key:
            put("look", "needs", f"{len(no_key)} AI shot{'s' if len(no_key) > 1 else ''} need keyframes generated.")
        elif waiting:
            put("look", "needs", f"{len(waiting)} AI still{'s' if len(waiting) > 1 else ''} waiting for your approval.")
        else:
            put("look", "done", "Stills reviewed." + (" All AI stills approved." if gen else ""))

    clips = {s.id for s in gen if (p.clips_dir / f"{s.id}.mp4").exists()}
    ready = [s.id for s in gen if ap.get(s.id, {}).get("approved") and s.id not in clips]
    if not gen:
        put("picture", "done", "No AI shots in this storyboard; nothing to generate.")
    elif clips == {s.id for s in gen}:
        put("picture", "done", f"All {len(gen)} AI shots have clips.")
    elif ready:
        put("picture", "needs", f"{len(ready)} approved shot{'s' if len(ready) > 1 else ''} ready to animate.")
    else:
        put("picture", "empty", "Approve AI stills first. Unanimated shots use their still with a slow push-in.")

    final = p.renders_dir / "final.mp4"
    deps = [p.storyboard, p.timing] + [p.clips_dir / f"{c}.mp4" for c in clips]
    if final.exists():
        newest = max(d.stat().st_mtime for d in deps if d.exists())
        put("render", "done" if final.stat().st_mtime >= newest else "stale",
            "Final render is current." if final.stat().st_mtime >= newest else "Things changed after the final render.")
    else:
        put("render", "empty", "Render a slice first, then the full song.")
    return st


def wave(p: Project, n: int = 360) -> list[float]:
    if not p.features.exists():
        return []
    rms = np.load(p.features)["rms"]
    edges = np.linspace(0, len(rms), n + 1).astype(int)
    return [round(float(rms[a:b].max()), 3) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])]


def _probe(path: Path) -> float | None:
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    try:
        return round(float(out.stdout.strip()), 2)
    except ValueError:
        return None


_probe_cache: dict[str, float | None] = {}


def renders(p: Project) -> list[dict]:
    if not p.renders_dir.exists():
        return []
    out = []
    for f in sorted(p.renders_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True):
        key = _mt(f) + str(f)
        if key not in _probe_cache:
            _probe_cache[key] = _probe(f)
        out.append({"name": f.stem, "file": f"renders/{f.name}", "duration": _probe_cache[key],
                    "size_mb": round(f.stat().st_size / 1e6, 1), "mtime": f.stat().st_mtime})
    return out


def history(p: Project, limit: int = 60) -> list[dict]:
    f = p.path("history.jsonl")
    if not f.exists():
        return []
    rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return rows[-limit:][::-1]


def log(p: Project, who: str, text: str) -> None:
    p.ensure()
    with p.path("history.jsonl").open("a") as fh:
        fh.write(json.dumps({"at": time.time(), "who": who, "text": text}) + "\n")


def _stamp(p: Project) -> float:
    best = 0.0
    for f in p.dir.glob("**/*"):
        try:
            if f.is_file() and not f.name.startswith(".") and f.suffix not in (".prog", ".tmp"):
                best = max(best, f.stat().st_mtime)
        except OSError:  # a temp file vanished mid-scan
            pass
    return best


def full(p: Project, running: set[str]) -> dict:
    def j(path, model):
        return p.read(path, model).model_dump(mode="json") if path.exists() else None

    try:
        audio = p.audio().name
    except FileNotFoundError:
        audio = None
    board = j(p.storyboard, Storyboard)
    files = {}
    if board:
        for s in board["shots"]:
            sid = s["id"]
            files[sid] = {k: f"stills/{n}" for k, n in [("thumb", f"{sid}_thumb.jpg"), ("still", f"{sid}.jpg"),
                                                         ("key", f"{sid}_key.png")] if (p.stills_dir / n).exists()}
            if (p.clips_dir / f"{sid}.mp4").exists():
                files[sid]["clip"] = f"clips/{sid}.mp4"
    return {
        "slug": p.slug, "song": j(p.song, SongSpec), "suno": j(p.suno, SunoResult), "audio": audio,
        "brief": p.path("brief.md").read_text() if p.path("brief.md").exists() else "",
        "timing": j(p.timing, Timing), "analysis": j(p.analysis, Analysis), "storyboard": board,
        "approvals": approvals(p), "shot_files": files, "wave": wave(p), "renders": renders(p),
        "status": statuses(p, running), "history": history(p),
        "stamp": _stamp(p),
    }


def summary(p: Project) -> dict:
    song = p.read(p.song, SongSpec) if p.song.exists() else None
    an = p.read(p.analysis, Analysis) if p.analysis.exists() else None
    board = p.read(p.storyboard, Storyboard) if p.storyboard.exists() else None
    cover = None
    if board:
        for s in board.shots[len(board.shots) // 2:] + board.shots:
            if (p.stills_dir / f"{s.id}_thumb.jpg").exists():
                cover = f"stills/{s.id}_thumb.jpg"
                break
    return {"slug": p.slug, "title": song.title if song else p.slug, "cover": cover,
            "duration": an.duration if an else None, "tempo": an.tempo if an else None,
            "status": {k: v["state"] for k, v in statuses(p, set()).items()}}
