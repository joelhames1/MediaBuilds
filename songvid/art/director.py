"""Claude as illustrator: writes the style kit and one canvas scene per shot.

Each scene is checked in the drawing browser right after it is written; if it throws, draws
nothing, or is too slow, the error goes back to Claude for a fix (up to two repairs).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..llm import ask_json, obj
from ..progress import report
from ..project import Project, load_config
from ..schemas import Analysis, ArtStyle, Shot, SongSpec, Storyboard
from .render import SceneError, art_dir, check_and_still, kit_path, scene_path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

PRESETS = {
    "simple animation": "flat vector shapes, limited palette, clean silhouettes, eased motion with squash and stretch",
    "continuous line drawing": "a single unbroken ink line drawing itself on warm paper, one or two ink colours",
    "watercolor": "soft translucent washes, bleeding edges, pigment pooling, visible paper grain, slow blooms",
    "ink and wash": "sumi-e brush strokes, black ink gradients, lots of empty paper, decisive gestures",
    "paper cut-out": "layered coloured paper shapes with soft drop shadows, slight wobble, stop-motion feel",
    "risograph print": "two or three spot colours, halftone grain, slight misregistration, bold simple shapes",
    "chalk on blackboard": "dusty chalk strokes, smudges, hand-drawn lines on a dark slate",
    "charcoal sketch": "smudgy charcoal marks, gestural shading, eraser highlights, off-white paper",
    "geometric minimal": "precise circles, lines and arcs, generous negative space, modernist poster feel",
    "neon line art": "glowing single-weight lines on near-black, soft bloom, sparse composition",
}

KIT_SCHEMA = obj({
    "kit_code": {"type": "string", "description": "JavaScript: helper functions and constants only"},
    "style_notes": {"type": "string", "description": "How the kit expresses the style, in 2-4 sentences"},
})
SCENE_SCHEMA = obj({
    "code": {"type": "string", "description": "JavaScript defining draw(ctx, t, S, A) and optionally setup(ctx, S)"},
    "notes": {"type": "string", "description": "One or two sentences on what the scene does"},
})


def system_prompt() -> str:
    return (PROMPTS / "art_director.md").read_text()


def style_text(st: ArtStyle) -> str:
    parts = []
    if st.preset:
        parts.append(f"Style: {st.preset}" + (f" ({PRESETS[st.preset]})" if st.preset in PRESETS else ""))
    if st.vibe:
        parts.append(f"Vibe and cues from the artist: {st.vibe}")
    if st.avoid:
        parts.append(f"Avoid: {st.avoid}")
    return "\n".join(parts) or "Style: your call, cinematic and hand-made rather than digital."


def write_kit(p: Project, cfg: dict) -> str:
    board = p.read(p.storyboard, Storyboard)
    song = p.read(p.song, SongSpec) if p.song.exists() else None
    palettes = sorted({tuple(s.palette) for s in board.shots if s.source == "claude"})[:6]
    user = "\n\n".join(x for x in [
        style_text(board.art or ArtStyle()),
        f"Song: {song.title}. {song.style}" if song else "",
        f"Video concept: {board.concept}" if board.concept else "",
        f"Palettes the shots use (dark to light): {json.dumps(palettes)}",
        "Write the style KIT now: the shared helpers every scene will call to look like one hand made it.",
    ] if x)
    report(None, "Claude is designing the style kit")
    data = ask_json(system_prompt(), user, KIT_SCHEMA, cfg)
    art_dir(p).mkdir(parents=True, exist_ok=True)
    kit_path(p).write_text(data["kit_code"])
    (art_dir(p) / "kit.notes.md").write_text(data["style_notes"])
    return data["kit_code"]


def _scene_brief(p: Project, board: Storyboard, sh: Shot, an: Analysis) -> str:
    k = next(i for i, s in enumerate(board.shots) if s.id == sh.id)
    prev = board.shots[k - 1] if k > 0 else None
    nxt = board.shots[k + 1] if k + 1 < len(board.shots) else None
    sec = next((s for s in an.sections if s.start <= sh.start + 0.01 < s.end), None)
    return "\n".join([
        f"Shot {sh.id}: {sh.end - sh.start:.2f} s, {sh.section} (section energy {sec.energy if sec else 0.5:.2f}), "
        f"{an.tempo:.0f} BPM, transition in: {sh.transition_in}, lyrics on screen: {'yes' if sh.lyrics_overlay else 'no'}.",
        f"What it shows: {sh.description}",
        f"Drawing brief: {sh.image_prompt or sh.description}",
        f"Motion brief: {sh.motion_prompt or 'your call'}",
        f"Palette (dark to light): {sh.palette}. Intensity {sh.intensity:.2f}, speed {sh.speed:.2f}.",
        f"Previous shot: {prev.description}" if prev else "This opens the video.",
        f"Next shot: {nxt.description}" if nxt else "This closes the video.",
    ])


def write_scene(p: Project, cfg: dict, sh: Shot, note: str = "", max_repairs: int = 2) -> dict:
    board = p.read(p.storyboard, Storyboard)
    an = p.read(p.analysis, Analysis)
    kit = kit_path(p).read_text()
    base = (f"{style_text(board.art or ArtStyle())}\n\nThe style KIT (already defined, call its helpers):\n```js\n{kit}\n```\n\n"
            + _scene_brief(p, board, sh, an))
    prev_code = scene_path(p, sh.id).read_text() if note and scene_path(p, sh.id).exists() else ""
    user = base + (f"\n\nYour previous code for this shot:\n```js\n{prev_code}\n```\nThe artist's note: {note}\nRevise it."
                   if prev_code else "\n\nWrite this shot's SCENE.")
    attempt, last_err = 0, None
    while True:
        data = ask_json(system_prompt(), user, SCENE_SCHEMA, cfg)
        try:
            stats = check_and_still(p, sh, data["code"])
            break
        except SceneError as e:
            last_err = str(e)
            attempt += 1
            print(f"  {sh.id}: scene check failed ({last_err}); attempt {attempt} to repair", file=sys.stderr)
            if attempt > max_repairs:
                raise RuntimeError(f"{sh.id}: Claude's scene still fails after {max_repairs} repairs: {last_err}")
            user = base + f"\n\nThis code:\n```js\n{data['code']}\n```\nfailed when run: {last_err}\nFix it. Keep the idea."
    art_dir(p).mkdir(parents=True, exist_ok=True)
    scene_path(p, sh.id).write_text(data["code"])
    (art_dir(p) / f"{sh.id}.notes.md").write_text(data["notes"])
    return {"id": sh.id, "repairs": attempt, **stats}


def draw_scenes(p: Project, shot_ids: list[str] | None = None, note: str = "", redo: bool = False) -> list[dict]:
    """Kit first (if missing), then scenes for Claude-drawn shots, a few in parallel."""
    from ..render.generate import _parallel, approve

    cfg = load_config(p)
    board = p.read(p.storyboard, Storyboard)
    if not kit_path(p).exists():
        write_kit(p, cfg)
    todo = [s for s in board.shots if s.source == "claude" and (not shot_ids or s.id in shot_ids)
            and (redo or note or not scene_path(p, s.id).exists())]
    try:
        return _parallel(todo, lambda sh: write_scene(p, cfg, sh, note), "scenes", cfg["art"]["concurrency"])
    finally:
        made = [s.id for s in todo if (p.stills_dir / f"{s.id}_key.png").exists()]
        if made:
            approve(p, made, value=False)  # new drawings need a fresh look
