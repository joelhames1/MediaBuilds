"""The JSON contracts between stages.

Every stage reads and writes one of these files in the project folder, so any
stage can be done by hand (or by Claude Code in chat) instead of by the CLI.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------- song.json ----------

class SunoSettings(BaseModel):
    model: str = "v6"
    variety: int = 0
    style_influence: int = 70
    weirdness: int = 30
    max_mode: bool = False
    vocal_gender: str | None = None


class SongSpec(BaseModel):
    title: str
    style: str
    style_terse: str = ""
    exclude: str = ""
    lyrics: str
    settings: SunoSettings = Field(default_factory=SunoSettings)
    notes: str = ""
    references: str = ""


# ---------- suno.json ----------

class SunoClip(BaseModel):
    id: str
    audio_url: str | None = None
    duration: float | None = None
    local_path: str | None = None


class SunoResult(BaseModel):
    mode: Literal["manual", "api"]
    task_id: str | None = None
    clips: list[SunoClip] = []
    chosen: str | None = None
    song_url: str | None = None


# ---------- timing.json ----------

class Word(BaseModel):
    text: str
    start: float
    end: float
    line: int
    confident: bool = True


class Line(BaseModel):
    index: int
    text: str
    section: str
    start: float
    end: float
    words: list[int]  # indexes into Timing.words


class Timing(BaseModel):
    source: str
    duration: float
    words: list[Word]
    lines: list[Line]


# ---------- analysis.json ----------

class Section(BaseModel):
    label: str
    kind: str  # intro / verse / chorus / bridge / outro / instrumental / ...
    start: float
    end: float
    energy: float  # 0..1 relative loudness


class Analysis(BaseModel):
    duration: float
    fps: int
    tempo: float
    beats: list[float]
    downbeats: list[float]
    sections: list[Section]
    features_file: str  # npz with per-frame rms/low/onset/centroid/beat


# ---------- storyboard.json ----------

SceneName = Literal["nebula", "smoke", "rays", "particles", "waves", "embers"]
Transition = Literal["cut", "fade", "flash"]
Source = Literal["procedural", "generated"]


class Shot(BaseModel):
    id: str
    start: float
    end: float
    section: str = ""
    description: str = ""
    source: Source = "procedural"
    # procedural renderer controls
    scene: SceneName = "nebula"
    palette: list[str] = Field(default_factory=lambda: ["#05060a", "#1b2a4a", "#c9a96e", "#f4e9d8"])
    intensity: float = 0.5
    speed: float = 0.5
    # generated-video controls
    image_prompt: str = ""
    motion_prompt: str = ""
    # shared
    transition_in: Transition = "cut"
    lyrics_overlay: bool = True
    punch: float = 0.3  # beat zoom-punch amount 0..1


class Storyboard(BaseModel):
    concept: str = ""
    look: str = ""  # global visual style, prepended to every image prompt
    font: str | None = None
    letterbox: bool = True
    # How lyrics appear: "words" sweeps word by word, "lines" fades whole lines in on their start.
    # "auto" uses lines when the timing is only line-level (word positions would be guesses).
    lyric_style: Literal["auto", "words", "lines"] = "auto"
    shots: list[Shot]
