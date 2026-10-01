"""Stage 1: craft lyrics + style for Suno (song.json + suno_paste.md)."""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path

from ..llm import ask_json, obj
from ..project import Project
from ..schemas import SongSpec, SunoSettings

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

SONG_SCHEMA = obj({
    "title": {"type": "string"},
    "style": {"type": "string", "description": "Full Style prompt, under ~700 characters"},
    "style_terse": {"type": "string", "description": "Short fallback Style prompt"},
    "exclude": {"type": "string", "description": "Comma-separated Exclude Styles list"},
    "lyrics": {"type": "string", "description": "Complete lyrics with [Section - cue] tags"},
    "settings": obj({
        "model": {"type": "string", "enum": ["v6", "v6-wild", "v6-mini"]},
        "variety": {"type": "integer"},
        "style_influence": {"type": "integer"},
        "weirdness": {"type": "integer"},
        "max_mode": {"type": "boolean"},
        "vocal_gender": {"type": "string", "enum": ["male", "female", "any"]},
    }),
    "notes": {"type": "string", "description": "Craft choices and what to try if the take misses"},
    "references": {"type": "string", "description": "Explain callbacks/references for the recipient"},
})


def find_skill(cfg: dict) -> Path | None:
    paths = cfg["llm"].get("suno_skill_path") or []
    for pattern in [paths] if isinstance(paths, str) else paths:
        for hit in sorted(glob.glob(os.path.expanduser(pattern), recursive=True)):
            return Path(hit)
    return None


def system_prompt(cfg: dict) -> str:
    skill = find_skill(cfg)
    base = skill.read_text() if skill else (PROMPTS / "songwriter.md").read_text()
    return (
        base
        + "\n\n---\nYou are running inside an automated pipeline. Return the song as JSON "
        "matching the schema instead of markdown blocks. Ask no questions; if the brief is thin, "
        "make a reasonable call and say what you chose in `notes`. The song will later become a "
        "music video, so concrete imagery in the lyrics is a plus."
    )


def generate(p: Project, brief: str, cfg: dict, revise: str | None = None) -> SongSpec:
    user = f"Brief:\n{brief}"
    if revise:
        prev = p.read(p.song, SongSpec)
        user = (
            f"Here is the current song:\n{prev.model_dump_json(indent=2)}\n\n"
            f"Revise it. Change one variable at a time unless told otherwise. Feedback:\n{revise}"
        )
    data = ask_json(system_prompt(cfg), user, SONG_SCHEMA, cfg)
    if data["settings"].get("vocal_gender") == "any":
        data["settings"]["vocal_gender"] = None
    spec = SongSpec.model_validate(data)
    save(p, spec, brief)
    return spec


def template(p: Project, brief: str) -> SongSpec:
    spec = SongSpec(
        title="Untitled",
        style="Genre, BPM. Vocal direction. Drums. Instruments. Arrangement. Production. Ending.",
        exclude="",
        lyrics="[Verse 1]\nWrite your first verse here\n\n[Chorus]\nAnd the chorus here\n\n[End]",
        notes="Template. Fill this in by hand, or ask Claude (with the Suno skill) to write it.",
    )
    save(p, spec, brief)
    return spec


def save(p: Project, spec: SongSpec, brief: str | None = None) -> None:
    p.ensure()
    if brief:
        p.path("brief.md").write_text(brief.strip() + "\n")
    p.write(p.song, spec)
    p.path("suno_paste.md").write_text(paste_blocks(spec))


def paste_blocks(s: SongSpec) -> str:
    st = s.settings
    out = [f"# {s.title}\n", "## Style\n```\n" + s.style + "\n```\n"]
    if s.style_terse:
        out.append("## Style (terse fallback)\n```\n" + s.style_terse + "\n```\n")
    out.append("## Exclude Styles\n```\n" + s.exclude + "\n```\n")
    out.append("## Lyrics\n```\n" + s.lyrics + "\n```\n")
    out.append(
        f"## Settings\n{st.model}, Variety {st.variety}, Style Influence {st.style_influence}, "
        f"Weirdness {st.weirdness}, Max Mode {'on' if st.max_mode else 'off'}"
        + (f", Vocal {st.vocal_gender}" if st.vocal_gender else "") + "\n"
    )
    for label, text in [("Notes", s.notes), ("References", s.references)]:
        if text:
            out.append(f"\n## {label}\n{text}\n")
    warn = [f"{n} is {len(v)} chars (limit {lim})" for n, v, lim in
            [("Style", s.style, 1000), ("Exclude", s.exclude, 1000), ("Lyrics", s.lyrics, 5000), ("Title", s.title, 100)]
            if len(v) > lim]
    if warn:
        out.append("\n> Over Suno field limits (silently truncated): " + "; ".join(warn) + "\n")
    return "\n".join(out)


# ---------- import from a chat transcript / markdown ----------

BLOCK_RE = re.compile(r"(?P<head>[^\n]*)\n+```[a-z]*\n(?P<body>.*?)```", re.S)


def import_markdown(p: Project, md: str) -> SongSpec:
    """Pull Style / Exclude / Lyrics / Settings out of paste-ready markdown (e.g. a Claude chat)."""
    found: dict[str, str] = {}
    for m in BLOCK_RE.finditer(md):
        head, body = m.group("head").lower(), m.group("body").strip()
        for key in ["terse", "exclude", "lyrics", "style", "settings", "title"]:
            if key in head and key not in found:
                found[key] = body
                break
        else:
            if "[verse" in body.lower() or "[chorus" in body.lower():
                found.setdefault("lyrics", body)
    if "lyrics" not in found or "style" not in found:
        raise ValueError("Could not find both a Style block and a Lyrics block in that markdown.")
    title = found.get("title") or next(
        (ln.lstrip("# ").strip() for ln in md.splitlines() if ln.startswith("# ")), "Untitled"
    )
    spec = SongSpec(
        title=title[:100],
        style=found["style"],
        style_terse=found.get("terse", ""),
        exclude=found.get("exclude", ""),
        lyrics=found["lyrics"],
        settings=parse_settings(found.get("settings", "") or md),
    )
    save(p, spec)
    return spec


def parse_settings(text: str) -> SunoSettings:
    s = SunoSettings()
    low = text.lower()
    if m := re.search(r"\b(v6-wild|v6-mini|v6)\b", low):
        s.model = m.group(1)
    for field, pat in [("variety", r"variety\D{0,4}(\d+)"),
                       ("style_influence", r"style influence\D{0,4}(\d+)"),
                       ("weirdness", r"weirdness\D{0,4}(\d+)")]:
        if m := re.search(pat, low):
            setattr(s, field, int(m.group(1)))
    if re.search(r"max mode\W{0,3}(on|yes)", low):
        s.max_mode = True
    return s
