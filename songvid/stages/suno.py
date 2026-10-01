"""Stage 2: get the song made by Suno.

manual: you paste suno_paste.md into suno.com, audition, download the keeper, then
        `songvid suno <slug> --import song.mp3` copies it in.
api:    a sunoapi.org-compatible third-party service generates it and also returns
        Suno's own word-level timing. Suno has no official public API; these services
        run generations on their own accounts, so check their terms and whether your
        Pro plan's commercial rights apply to songs made that way.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import requests

from ..project import Project, load_config
from ..schemas import SongSpec, SunoClip, SunoResult


def import_audio(p: Project, src: Path, aligned: Path | None = None, url: str | None = None) -> SunoResult:
    p.ensure()
    for old in p.dir.glob("audio.*"):
        old.unlink()
    dest = p.path("audio" + src.suffix.lower())
    shutil.copyfile(src, dest)
    if aligned:
        shutil.copyfile(aligned, p.suno_aligned)
    res = SunoResult(mode="manual", clips=[SunoClip(id=src.stem, local_path=dest.name)],
                     chosen=src.stem, song_url=url)
    p.write(p.suno, res)
    return res


# ---------- third-party API ----------

class SunoAPI:
    def __init__(self, cfg: dict):
        self.cfg = cfg["suno"]
        key = os.environ.get("SUNOAPI_KEY")
        if not key:
            raise RuntimeError("Set SUNOAPI_KEY to use --mode api (or use the manual flow).")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        self.base = self.cfg["api_base"].rstrip("/")

    def _ok(self, r: requests.Response) -> dict:
        r.raise_for_status()
        body = r.json()
        if body.get("code") not in (200, None):
            raise RuntimeError(f"Suno API error {body.get('code')}: {body.get('msg')}")
        return body.get("data") or {}

    def generate(self, spec: SongSpec) -> str:
        payload = {
            "customMode": True,
            "instrumental": False,
            "model": self.cfg["api_model"],
            "title": spec.title[:100],
            "style": spec.style[:1000],
            "negativeTags": spec.exclude[:1000],
            "prompt": spec.lyrics[:5000],
            "callBackUrl": self.cfg["callback_url"],
        }
        if spec.settings.vocal_gender:
            payload["vocalGender"] = "m" if spec.settings.vocal_gender == "male" else "f"
        # Slider names on third-party APIs are 0..1 floats where supported.
        payload["styleWeight"] = spec.settings.style_influence / 100
        payload["weirdnessConstraint"] = spec.settings.weirdness / 100
        return self._ok(self.s.post(f"{self.base}/api/v1/generate", json=payload, timeout=60))["taskId"]

    def wait(self, task_id: str) -> list[dict]:
        deadline = time.time() + self.cfg["timeout_seconds"]
        while time.time() < deadline:
            data = self._ok(self.s.get(f"{self.base}/api/v1/generate/record-info",
                                       params={"taskId": task_id}, timeout=60))
            status = data.get("status", "")
            print(f"  suno task {task_id}: {status}", file=sys.stderr)
            if status == "SUCCESS":
                return (data.get("response") or {}).get("sunoData") or []
            if "FAILED" in status or "ERROR" in status or status.endswith("_EXCEPTION"):
                raise RuntimeError(f"Suno generation failed: {status} {data.get('errorMessage')}")
            time.sleep(self.cfg["poll_seconds"])
        raise TimeoutError("Suno generation timed out")

    def aligned_words(self, task_id: str, audio_id: str) -> dict:
        return self._ok(self.s.post(f"{self.base}/api/v1/generate/get-timestamped-lyrics",
                                    json={"taskId": task_id, "audioId": audio_id}, timeout=60))


def run_api(p: Project, cfg: dict, pick: int = 0) -> SunoResult:
    spec = p.read(p.song, SongSpec)
    api = SunoAPI(cfg)
    task_id = api.generate(spec)
    print(f"  submitted task {task_id}", file=sys.stderr)
    clips = []
    for i, d in enumerate(api.wait(task_id)):
        url = d.get("audioUrl") or d.get("sourceAudioUrl") or d.get("streamAudioUrl")
        local = p.path(f"take_{i + 1}.mp3")
        if url:
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            local.write_bytes(r.content)
        clips.append(SunoClip(id=d["id"], audio_url=url, duration=d.get("duration"),
                              local_path=local.name if url else None))
    if not clips:
        raise RuntimeError("Suno returned no clips")
    chosen = clips[min(pick, len(clips) - 1)]
    res = SunoResult(mode="api", task_id=task_id, clips=clips, chosen=chosen.id)
    choose(p, res, chosen.id, api)
    return res


def choose(p: Project, res: SunoResult, clip_id: str, api: SunoAPI | None = None) -> None:
    """Make one of the takes the active audio (+ its Suno timing, when available)."""
    clip = next(c for c in res.clips if c.id == clip_id)
    for old in p.dir.glob("audio.*"):
        old.unlink()
    shutil.copyfile(p.path(clip.local_path), p.path("audio.mp3"))
    res.chosen = clip_id
    if res.mode == "api" and res.task_id:
        try:
            api = api or SunoAPI(load_config(p))
            p.suno_aligned.write_text(json.dumps(api.aligned_words(res.task_id, clip_id), indent=1))
        except Exception as e:  # timing is a bonus; whisper/even alignment still works
            print(f"  could not fetch Suno timing ({e}); will force-align instead", file=sys.stderr)
            p.suno_aligned.unlink(missing_ok=True)
    p.write(p.suno, res)
