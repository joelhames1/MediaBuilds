"""The Claude panel: Claude directs by editing storyboard.json / song.json through tools.

Conversation history is kept server-side per project and appended unchanged (thinking
blocks included), as the API requires. Each turn snapshots the files it may touch so
the UI can offer one-click undo.
"""

from __future__ import annotations

import bisect
import json
from pathlib import Path
from typing import Callable

import anthropic

from ..llm import LLMUnavailable, describe, drain, record
from ..project import Project, load_config
from ..schemas import Analysis, Shot, SongSpec, Storyboard, Timing
from ..stages.storyboard import tidy
from . import state

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
SHOT_FIELDS = {"section", "description", "source", "scene", "palette", "intensity", "speed", "image_prompt",
               "motion_prompt", "transition_in", "lyrics_overlay", "punch"}

CHAT_RULES = """
You are the Claude panel inside Cuesheet, the artist's music-video studio. You see the song's
timed lyrics, sections, bar lines and the current shot list via get_project, and you change things
with tools. The UI shows your changes on the timeline immediately.

- Make the change, then reply in one to three plain sentences saying what you changed and why.
  No headings, no lists unless asked.
- Keep cuts on bar lines (the tools snap for you) and keep the look consistent.
- You may queue cheap steps with run_step (stills, keyframes, preview renders, refit). Never spend
  on video generation or Suno; tell the artist which button to press instead.
- If a request is ambiguous, make a reasonable call and say what you chose.
"""

TOOLS = [
    {"name": "get_project", "description": "Current song, timed lyric lines, sections, bar lines, shots, approvals and step status.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "update_shots", "description": "Change fields on one or more shots. Allowed fields: " + ", ".join(sorted(SHOT_FIELDS)) +
     ". scene is one of nebula, smoke, rays, particles, waves, embers. source is procedural, claude (hand-drawn "
     "by Claude in the video's drawing style) or generated (AI video, costs money). "
     "transition_in is cut, fade or flash. palette is 3-5 hex colors dark to bright. Numbers are 0..1.",
     "input_schema": {"type": "object", "properties": {"changes": {"type": "array", "items": {"type": "object", "properties": {
         "id": {"type": "string"}, "fields": {"type": "object"}}, "required": ["id", "fields"]}}}, "required": ["changes"]}},
    {"name": "split_shot", "description": "Split a shot into two at the bar line nearest `at` (seconds). The new second half gets id <id>b and copies the first half's settings.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "at": {"type": "number"}}, "required": ["id", "at"]}},
    {"name": "merge_with_next", "description": "Merge a shot with the one after it; the first shot's settings win.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "move_cut", "description": "Move the start of a shot (not the first) to the bar line nearest `start`.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "start": {"type": "number"}}, "required": ["id", "start"]}},
    {"name": "update_song", "description": "Edit the Suno song sheet. Changing lyrics makes timing and everything after it out of date.",
     "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "style": {"type": "string"},
                      "style_terse": {"type": "string"}, "exclude": {"type": "string"}, "lyrics": {"type": "string"}}}},
    {"name": "run_step", "description": "Queue a cheap pipeline step: stills (re-render stills), keyframes (AI stills for generated shots, small cost), "
     "draw (Claude draws the Claude-drawn shots that have no drawing yet), "
     "refit (re-fit shots to new bar lines), preview (render a preview slice from start to end seconds).",
     "input_schema": {"type": "object", "properties": {"step": {"type": "string", "enum": ["stills", "keyframes", "draw", "refit", "preview"]},
                      "start": {"type": "number"}, "end": {"type": "number"}}, "required": ["step"]}},
]

_sessions: dict[str, list] = {}
_undo: dict[str, dict] = {}


def _snap(an: Analysis, t: float) -> float:
    grid = sorted(an.downbeats)
    i = bisect.bisect_left(grid, t)
    near = grid[max(0, i - 1): i + 1] or [t]
    return min(near, key=lambda g: abs(g - t))


def _project_view(p: Project, running: set[str]) -> dict:
    out = {}
    if p.song.exists():
        s = p.read(p.song, SongSpec)
        out["song"] = {"title": s.title, "style": s.style, "exclude": s.exclude, "lyrics": s.lyrics}
    if p.analysis.exists():
        an = p.read(p.analysis, Analysis)
        out["duration"], out["tempo"] = an.duration, an.tempo
        out["sections"] = [s.model_dump() for s in an.sections]
        out["bar_lines"] = [round(b, 2) for b in an.downbeats]
    if p.timing.exists():
        tm = p.read(p.timing, Timing)
        out["lines"] = [{"i": ln.index, "start": ln.start, "end": ln.end, "section": ln.section, "text": ln.text} for ln in tm.lines]
    if p.storyboard.exists():
        out["storyboard"] = p.read(p.storyboard, Storyboard).model_dump()
    out["approvals"] = state.approvals(p)
    out["status"] = {k: v["state"] for k, v in state.statuses(p, running).items()}
    return out


def turn(p: Project, message: str, running: set[str], queue_step: Callable[[str, dict], int]) -> dict:
    drain()
    out = _turn(p, message, running, queue_step)
    out["ai"] = describe(drain())
    if out["ai"]:
        state.log(p, "claude", f"Claude panel: {out['ai']}")
    return out


def _turn(p: Project, message: str, running: set[str], queue_step: Callable[[str, dict], int]) -> dict:
    cfg = load_config(p)
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError as e:
        raise LLMUnavailable(str(e)) from e
    msgs = _sessions.setdefault(p.slug, [])
    snap = {f.name: f.read_text() for f in [p.storyboard, p.song] if f.exists()}
    tid = f"t{len(msgs)}"
    _undo.setdefault(p.slug, {})[tid] = snap
    msgs.append({"role": "user", "content": message})
    system = (PROMPTS / "director.md").read_text() + "\n" + CHAT_RULES
    changes: list[str] = []
    jobs: list[int] = []

    for _ in range(10):
        try:
            resp = client.messages.create(model=cfg["llm"]["model"], max_tokens=16000, system=system, tools=TOOLS,
                                          messages=msgs, output_config={"effort": "medium"})
        except TypeError as e:
            msgs.pop()
            raise LLMUnavailable("No Anthropic credentials. Add your key in Settings.") from e
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            msgs.pop()
            raise LLMUnavailable("Anthropic rejected the key. Check it in Settings.") from e
        record(resp, cfg["llm"]["model"])
        msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "refusal":
            return {"reply": "Claude declined that request.", "changes": changes, "turn": tid, "jobs": jobs}
        uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            reply = " ".join(b.text for b in resp.content if b.type == "text").strip()
            if changes:
                state.log(p, "claude", reply.split(". ")[0][:140] or "; ".join(changes))
            return {"reply": reply, "changes": changes, "turn": tid if changes else None, "jobs": jobs}
        results = []
        for u in uses:
            try:
                out = _tool(p, u.name, u.input, running, queue_step, changes, jobs)
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(out)[:60000]})
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": f"Error: {e}", "is_error": True})
        msgs.append({"role": "user", "content": results})
    return {"reply": "I made several changes but ran out of steps. Check the timeline.", "changes": changes, "turn": tid, "jobs": jobs}


def _tool(p, name, inp, running, queue_step, changes, jobs):
    if name == "get_project":
        return _project_view(p, running)
    if name == "update_song":
        spec = p.read(p.song, SongSpec)
        for k in ("title", "style", "style_terse", "exclude", "lyrics"):
            if k in inp:
                setattr(spec, k, inp[k])
                changes.append(f"song.{k}")
        from ..stages.song import save
        save(p, spec)
        return {"ok": True}
    if name == "run_step":
        step = inp["step"]
        jid = queue_step(step, inp)
        jobs.append(jid)
        changes.append(f"queued {step}")
        return {"queued_job": jid}
    board = p.read(p.storyboard, Storyboard)
    an = p.read(p.analysis, Analysis)
    by = {s.id: s for s in board.shots}
    if name == "update_shots":
        for ch in inp["changes"]:
            sh = by.get(ch["id"])
            if not sh:
                raise ValueError(f"No shot {ch['id']}")
            bad = set(ch["fields"]) - SHOT_FIELDS
            if bad:
                raise ValueError(f"Not editable: {', '.join(bad)}")
            data = sh.model_dump() | ch["fields"]
            by[ch["id"]] = Shot.model_validate(data)
            changes.append(f"{ch['id']}: " + ", ".join(f"{k} → {v}" for k, v in ch["fields"].items() if k not in ("image_prompt", "motion_prompt", "description"))
                           or f"{ch['id']}: prompts")
        board.shots = [by[s.id] for s in board.shots]
    elif name == "split_shot":
        sh = by[inp["id"]]
        at = _snap(an, inp["at"])
        if not sh.start + 1 < at < sh.end - 1:
            raise ValueError(f"{sh.id} runs {sh.start:.2f}-{sh.end:.2f}; no bar line far enough inside it near {inp['at']}")
        new_id = next(sh.id + c for c in "bcdefghjk" if sh.id + c not in by)
        b = sh.model_copy(update={"id": new_id, "start": at, "transition_in": "cut"})
        sh.end = at
        board.shots.insert(board.shots.index(sh) + 1, b)
        changes.append(f"split {sh.id} at {at:.2f}s")
    elif name == "merge_with_next":
        k = next(i for i, s in enumerate(board.shots) if s.id == inp["id"])
        if k + 1 >= len(board.shots):
            raise ValueError("That is the last shot")
        board.shots[k].end = board.shots[k + 1].end
        changes.append(f"merged {board.shots[k + 1].id} into {inp['id']}")
        del board.shots[k + 1]
    elif name == "move_cut":
        k = next(i for i, s in enumerate(board.shots) if s.id == inp["id"])
        if k == 0:
            raise ValueError("The first shot always starts at 0")
        t = _snap(an, inp["start"])
        if not board.shots[k - 1].start + 1 < t < board.shots[k].end - 1:
            raise ValueError("That would make a shot shorter than a second")
        board.shots[k - 1].end = board.shots[k].start = t
        changes.append(f"moved cut before {inp['id']} to {t:.2f}s")
    else:
        raise ValueError(f"Unknown tool {name}")
    # keep ids stable (tidy renumbers), just enforce contiguity
    ids = [s.id for s in board.shots]
    board = tidy(board, an)
    for s, i in zip(board.shots, ids):
        s.id = i
    p.write(p.storyboard, board)
    return {"ok": True, "shots": [{"id": s.id, "start": s.start, "end": s.end} for s in board.shots]}


def undo(p: Project, tid: str) -> bool:
    snap = _undo.get(p.slug, {}).pop(tid, None)
    if not snap:
        return False
    for name, text in snap.items():
        p.path(name).write_text(text)
    if p.song.exists():
        from ..stages.song import save
        save(p, p.read(p.song, SongSpec))
    msgs = _sessions.get(p.slug)
    if msgs and msgs[-1]["role"] == "assistant":
        msgs.append({"role": "user", "content": "(The artist undid your last change.)"})
        msgs.append({"role": "assistant", "content": "Understood, that change is reverted."})
    state.log(p, "you", "Undid Claude's last change")
    return True


def reset(slug: str) -> None:
    _sessions.pop(slug, None)
