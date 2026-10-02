"""Stage 5: shot list (storyboard.json), by Claude or by a heuristic fallback."""

from __future__ import annotations

import bisect
import json
from pathlib import Path

from ..llm import LLMUnavailable, ask_json, obj
from ..project import Project
from ..schemas import Analysis, ArtStyle, Shot, SongSpec, Storyboard, Timing

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
SCENES = ["nebula", "smoke", "rays", "particles", "waves", "embers"]

SHOT_SCHEMA = obj({
    "id": {"type": "string"},
    "start": {"type": "number"},
    "end": {"type": "number"},
    "section": {"type": "string"},
    "description": {"type": "string", "description": "What the viewer sees and why, one or two sentences"},
    "source": {"type": "string", "enum": ["procedural", "generated", "claude"]},
    "scene": {"type": "string", "enum": SCENES},
    "palette": {"type": "array", "items": {"type": "string"}},
    "intensity": {"type": "number"},
    "speed": {"type": "number"},
    "image_prompt": {"type": "string"},
    "motion_prompt": {"type": "string"},
    "transition_in": {"type": "string", "enum": ["cut", "fade", "flash"]},
    "lyrics_overlay": {"type": "boolean"},
    "punch": {"type": "number"},
})
BOARD_SCHEMA = obj({
    "concept": {"type": "string", "description": "The idea of the video in 2-4 sentences"},
    "look": {"type": "string", "description": "Global visual style, prepended to every image prompt"},
    "letterbox": {"type": "boolean"},
    "shots": {"type": "array", "items": SHOT_SCHEMA},
})

MODE_NOTE = {
    "internal": 'Use source "procedural" for every shot. image_prompt/motion_prompt may be empty.',
    "external": 'Use source "generated" for every shot, but still pick a procedural scene + palette as a '
                'fallback. Shots should be 3 to 10 seconds (generated clips come in 5 s and 10 s lengths).',
    "claude": 'Use source "claude" for every shot: Claude will hand-draw each one as an animated illustration '
              'in the video\'s chosen style. Write image_prompt as a concrete drawing brief (subject, composition, '
              'what is on paper) and motion_prompt as how it animates over the shot. Keep ideas drawable: clear '
              'subjects, simple compositions, one idea per shot. Shots of 2 to 8 bars.',
    "hybrid": 'Mix sources: "generated" for the moments that need real imagery (key lyric images, '
              'chorus peaks), "procedural" for transitions, intros and instrumental passages. Keep '
              'generated shots 3 to 10 seconds and use them for at most about half the runtime.',
}


def _context(spec: SongSpec, tm: Timing | None, an: Analysis) -> str:
    bars = [round(b, 2) for b in an.downbeats]
    parts = [
        f"Title: {spec.title}",
        f"Suno style prompt: {spec.style}",
        f"Duration: {an.duration:.2f} s, tempo {an.tempo:.0f} BPM",
        "Sections (label, start, end, energy 0..1):",
        *[f"  {s.label}: {s.start:.2f}-{s.end:.2f} energy {s.energy:.2f}" for s in an.sections],
        f"Downbeat (bar line) times: {json.dumps(bars)}",
    ]
    if tm:
        parts.append("Timed lyric lines (index, start-end, section, text):")
        parts += [f"  {ln.index}: {ln.start:.2f}-{ln.end:.2f} [{ln.section}] {ln.text}" for ln in tm.lines]
    return "\n".join(parts)


def generate(p: Project, cfg: dict, mode: str, direction: str = "", art: ArtStyle | None = None) -> Storyboard:
    spec = p.read(p.song, SongSpec)
    an = p.read(p.analysis, Analysis)
    tm = p.read(p.timing, Timing) if p.timing.exists() else None
    art = art or (p.read(p.storyboard, Storyboard).art if p.storyboard.exists() else None)
    user = (_context(spec, tm, an) + f"\n\nMode: {mode}. {MODE_NOTE[mode]}"
            + (f"\n\nDrawing style for Claude-drawn shots: {art.preset} {art.vibe} (avoid: {art.avoid or 'nothing listed'})"
               if mode == "claude" and art else "")
            + (f"\n\nDirection from the artist:\n{direction}" if direction else ""))
    try:
        data = ask_json((PROMPTS / "director.md").read_text(), user, BOARD_SCHEMA, cfg)
        for i, sh in enumerate(data.get("shots") or []):
            sh["id"] = f"s{i + 1:02d}"  # tidy renumbers anyway; ids become file names, so never trust them
        board = Storyboard.model_validate(data)
    except LLMUnavailable as e:
        print(f"  no Claude ({e}); using the heuristic storyboard")
        board = heuristic(an, mode)
    board = tidy(board, an)
    board.art = art or board.art
    p.write(p.storyboard, board)
    return board


def heuristic(an: Analysis, mode: str = "internal") -> Storyboard:
    """Rule-based storyboard: scenes by section kind, cuts every few bars."""
    by_kind = {
        "intro": "smoke", "verse": "nebula", "pre-chorus": "particles", "chorus": "rays",
        "post-chorus": "rays", "hook": "rays", "bridge": "waves", "breakdown": "smoke",
        "instrumental": "particles", "outro": "embers", "end": "embers",
    }
    base = ["#04050a", "#16233f", "#5b4a7a", "#d9a65b", "#fbf1dc"]
    warm = ["#0a0505", "#3a1420", "#a3402c", "#f2a541", "#fff3d6"]
    bar = 60 / max(an.tempo, 1) * 4
    shots: list[Shot] = []
    seen_chorus = False
    for sec in an.sections:
        kind = sec.kind
        scene = by_kind.get(kind, "nebula")
        hot = kind in ("chorus", "hook", "post-chorus")
        length = bar * (2 if sec.energy > 0.6 else 4)
        t, alt = sec.start, 0
        while t < sec.end - 0.5:
            end = min(sec.end, t + length)
            if sec.end - end < length * 0.5:
                end = sec.end
            first = t == sec.start
            sc = scene if alt % 2 == 0 or not hot else "particles"
            shots.append(Shot(
                id=f"s{len(shots) + 1:02d}", start=t, end=end, section=sec.label,
                description=f"{sec.label}: {sc}", scene=sc,
                source="claude" if mode == "claude" else
                       "generated" if mode == "external" or (mode == "hybrid" and hot) else "procedural",
                palette=warm if hot and seen_chorus else base,
                intensity=round(0.3 + 0.7 * sec.energy, 2), speed=round(0.3 + 0.5 * sec.energy, 2),
                image_prompt=f"abstract cinematic {scene}, {sec.label.lower()} mood",
                motion_prompt="slow push in, drifting light",
                transition_in=("flash" if hot else "fade") if first and shots else "cut",
                lyrics_overlay=kind not in ("verse",), punch=0.15 + 0.3 * sec.energy,
            ))
            t, alt = end, alt + 1
        seen_chorus |= hot
    return Storyboard(concept="Heuristic storyboard (no Claude). Scenes follow section types.",
                      look="cinematic, abstract, filmic grain, deep shadows", shots=shots)


def tidy(board: Storyboard, an: Analysis) -> Storyboard:
    """Sort, snap cuts to nearby bar lines, and make shots contiguous over [0, duration]."""
    shots = sorted(board.shots, key=lambda s: s.start)
    grid = sorted(set(an.downbeats) | {s.start for s in an.sections})
    for sh in shots[1:]:
        i = bisect.bisect_left(grid, sh.start)
        near = [g for g in grid[max(0, i - 1): i + 1] if abs(g - sh.start) < 0.6]
        if near:
            sh.start = min(near, key=lambda g: abs(g - sh.start))
    keep = [s for i, s in enumerate(shots) if i == 0 or s.start - shots[i - 1].start >= 0.5]
    for i, sh in enumerate(keep):
        sh.start = 0.0 if i == 0 else round(sh.start, 3)
        sh.end = round(keep[i + 1].start, 3) if i + 1 < len(keep) else an.duration
        sh.id = f"s{i + 1:02d}"
        sh.intensity = min(1.0, max(0.0, sh.intensity))
        sh.speed = min(1.0, max(0.0, sh.speed))
        sh.punch = min(1.0, max(0.0, sh.punch))
        sh.palette = [c for c in sh.palette if _is_hex(c)] or ["#05060a", "#334", "#c9a96e", "#fff"]
    board.shots = keep
    return board


def _is_hex(c: str) -> bool:
    c = c.lstrip("#")
    return len(c) in (3, 6) and all(ch in "0123456789abcdefABCDEF" for ch in c)
