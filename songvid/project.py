"""Project folder layout and config loading."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = Path(os.environ.get("SONGVID_PROJECTS", REPO_ROOT / "projects"))

DEFAULT_CONFIG = {
    "llm": {
        "model": "claude-opus-5-5",
        "effort": "high",
        "fallbacks": True,
        # Path to the local Suno songwriting skill. Used as the songwriter's
        # system prompt when found. Glob patterns are allowed.
        "suno_skill_path": [
            "~/.claude/skills/**/suno-v6-songwriting/SKILL.md",
            "/root/.claude/skills/**/suno-v6-songwriting/SKILL.md",
        ],
    },
    "suno": {
        "mode": "manual",  # manual | api
        # Third-party Suno API (sunoapi.org-compatible). Unofficial: Suno has no public API.
        "api_base": "https://api.sunoapi.org",
        "api_model": "V5",
        "callback_url": "https://example.com/songvid-callback",
        "poll_seconds": 15,
        "timeout_seconds": 900,
    },
    "align": {
        "method": "auto",  # auto | suno | whisper | even
        "whisper_model": "medium",
        "isolate_vocals": False,
    },
    "video": {
        "width": 1920,
        "height": 1080,
        "fps": 24,
        "render_scale": 0.5,  # procedural scenes render at this fraction, then upscale
        "crf": 18,
        "font": None,  # path to a .ttf; None = auto-pick a serif italic
        "workers": max(1, (os.cpu_count() or 2) - 1),
    },
    "generate": {
        "provider": "fal",
        "image_model": "fal-ai/flux/dev",
        "video_model": "fal-ai/kling-video/v3/standard/image-to-video",
        # Request field names differ per model; adjust here instead of in code.
        "image_args": {"image_size": "landscape_16_9", "num_images": 1},
        "video_args": {},
        "video_image_field": "image_url",
        "video_durations": [5, 10],
        "poll_seconds": 5,
        "timeout_seconds": 900,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(project: "Project | None" = None) -> dict:
    cfg = DEFAULT_CONFIG
    for path in [REPO_ROOT / "songvid.yaml", project.dir / "songvid.yaml" if project else None]:
        if path and path.exists():
            cfg = _merge(cfg, yaml.safe_load(path.read_text()) or {})
    return cfg


M = TypeVar("M", bound=BaseModel)


@dataclass
class Project:
    slug: str

    @property
    def dir(self) -> Path:
        return PROJECTS_DIR / self.slug

    def path(self, name: str) -> Path:
        return self.dir / name

    # well-known files
    song = property(lambda self: self.path("song.json"))
    suno = property(lambda self: self.path("suno.json"))
    suno_aligned = property(lambda self: self.path("suno_aligned.json"))
    timing = property(lambda self: self.path("timing.json"))
    analysis = property(lambda self: self.path("analysis.json"))
    features = property(lambda self: self.path("features.npz"))
    storyboard = property(lambda self: self.path("storyboard.json"))
    stills_dir = property(lambda self: self.path("stills"))
    clips_dir = property(lambda self: self.path("clips"))
    renders_dir = property(lambda self: self.path("renders"))

    def audio(self) -> Path:
        for name in ["audio.mp3", "audio.wav", "audio.m4a", "audio.flac"]:
            p = self.path(name)
            if p.exists():
                return p
        raise FileNotFoundError(
            f"No audio in {self.dir}. Run `songvid suno {self.slug} --import <file.mp3>` first."
        )

    def ensure(self) -> "Project":
        self.dir.mkdir(parents=True, exist_ok=True)
        return self

    def read(self, path: Path, model: type[M]) -> M:
        if not path.exists():
            raise FileNotFoundError(f"Missing {path.name} in {self.dir}")
        return model.model_validate_json(path.read_text())

    def write(self, path: Path, obj: BaseModel | dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return path
