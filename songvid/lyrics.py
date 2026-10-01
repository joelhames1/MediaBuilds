"""Parse Suno-style lyrics ([Section - cue] tags + lines) into sung lines and words."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TAG_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
INLINE_TAG_RE = re.compile(r"\[[^\]]*\]")
WORD_RE = re.compile(r"[A-Za-z0-9À-ɏ']+(?:-[A-Za-z0-9À-ɏ']+)*")

NON_SUNG = {"instrumental", "intro", "outro", "break", "interlude", "solo", "drop", "end", "fade out", "fade"}

KINDS = ["pre-chorus", "post-chorus", "chorus", "verse", "bridge", "hook", "intro", "outro", "refrain",
         "breakdown", "interlude", "instrumental", "solo", "drop", "end"]


def section_kind(label: str) -> str:
    low = label.lower()
    for k in KINDS:
        if k in low:
            return k
    return "section"


@dataclass
class LyricLine:
    index: int
    text: str
    section: str
    words: list[str] = field(default_factory=list)


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower().replace("-", ""))


def parse(lyrics: str) -> list[LyricLine]:
    lines: list[LyricLine] = []
    section = "Intro"
    for raw in lyrics.splitlines():
        m = TAG_RE.match(raw)
        if m:
            section = m.group(1).split(" - ")[0].split(":")[0].strip()
            continue
        text = INLINE_TAG_RE.sub("", raw).strip()
        if not text:
            continue
        # Hyphenated stretches like "sta-a-ay" are one sung word.
        words = [w for w in WORD_RE.findall(text) if norm(w)]
        if words:
            lines.append(LyricLine(len(lines), text, section, words))
    return lines
