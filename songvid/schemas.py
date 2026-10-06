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
Source = Literal["procedural", "generated", "claude"]


class Shot(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")  # used in file names (stills, clips, scenes)
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
    # Which video model animates this shot: a name from `generate.video_models` in config, or a raw
    # fal model id. Empty uses the default model.
    video_model: str = ""
    # Reference images (paths inside the project) handed to the image model so recurring characters
    # look the same from shot to shot.
    refs: list[str] = Field(default_factory=list)
    # shared
    transition_in: Transition = "cut"
    lyrics_overlay: bool = True
    punch: float = 0.3  # beat zoom-punch amount 0..1
    letterbox: bool | None = None  # None follows the storyboard; False shows this shot full frame (e.g. "TV")


OverlayKind = Literal["chyron", "ticker", "bug", "caption", "card", "vs"]


class Overlay(BaseModel):
    """Broadcast graphics drawn over the picture, timed in absolute song seconds."""
    kind: OverlayKind
    start: float
    end: float
    text: str = ""         # chyron headline, caption, card title, bug channel name, VS left name
    sub: str = ""          # second line
    tag: str = ""          # chyron kicker ("BREAKING NEWS"), ticker label, bug label
    text2: str = ""        # VS right-hand name
    sub2: str = ""
    items: list[str] = Field(default_factory=list)  # ticker headlines
    style: str = ""        # chyron: breaking | update | developing | live | expert; caption: serif; card: scrim
    pos: tuple[float, float] | None = None  # caption centre as fractions of the frame
    size: int | None = None  # caption / card text size at 1080p
    speed: float | None = None  # ticker pixels per second at 1080p
    fade: float | None = None  # card fade in/out seconds


class ArtStyle(BaseModel):
    """Look for Claude-drawn shots: a preset name and/or the artist's own words."""
    preset: str = ""
    vibe: str = ""
    avoid: str = ""


class Storyboard(BaseModel):
    concept: str = ""
    look: str = ""  # global visual style, prepended to every image prompt
    font: str | None = None
    letterbox: bool = True
    # How lyrics appear: "words" sweeps word by word, "lines" fades whole lines in on their start.
    # "auto" uses lines when the timing is only line-level (word positions would be guesses).
    lyric_style: Literal["auto", "words", "lines"] = "auto"
    art: ArtStyle | None = None
    overlays: list[Overlay] = Field(default_factory=list)
    # Sung words to bleep: a black bar over the lyric and a tone over the vocal (unless rendering uncensored)
    censor: list[str] = Field(default_factory=list)
    shots: list[Shot]
