"""ElevenLabs: designed character voices, spoken lines with word timing, and sound effects.

Driven by `cast.json` in the project:
{
  "voices": {"anchor": {"description": "...", "sample": "100+ chars the voice reads while being designed"}},
  "lines":  [{"id": "anchor_open", "voice": "anchor", "text": "[serious] Good evening...", "takes": 3,
              "bleep_words": ["fucking"], "settings": {"stability": 0.5}}],
  "sfx":    [{"id": "hooves_walk", "text": "...", "seconds": 4, "influence": 0.6, "takes": 2, "loop": false}]
}
Designed voices are saved to your ElevenLabs account once and their ids written to `voices.json`.
Every result is cached by its inputs, so re-running only pays for what changed. Voices are always
designed from a description; this module never clones a real person.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import requests

from .project import Project

API = "https://api.elevenlabs.io/v1"
TTS_MODEL = "eleven_v3"
DESIGN_MODEL = "eleven_ttv_v3"
SFX_MODEL = "eleven_text_to_sound_v2"
FORMAT = "mp3_44100_192"


class Eleven:
    def __init__(self):
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError("Set ELEVENLABS_API_KEY (https://elevenlabs.io/app/settings/api-keys).")
        self.s = requests.Session()
        self.s.headers.update({"xi-api-key": key})

    def _post(self, path: str, body: dict, params: dict | None = None, raw: bool = False):
        r = self.s.post(f"{API}{path}", json=body, params=params, timeout=300)
        if not r.ok:
            raise RuntimeError(f"ElevenLabs {path} failed ({r.status_code}): {r.text[:300]}")
        return r.content if raw else r.json()

    def design(self, description: str, sample: str, seed: int | None = None) -> list[dict]:
        body = {"voice_description": description, "model_id": DESIGN_MODEL, "text": sample}
        if seed is not None:
            body["seed"] = seed
        return self._post("/text-to-voice/design", body)["previews"]

    def save_voice(self, name: str, description: str, generated_voice_id: str) -> str:
        res = self._post("/text-to-voice", {"voice_name": name, "voice_description": description,
                                            "generated_voice_id": generated_voice_id})
        return res["voice_id"]

    def speak(self, voice_id: str, text: str, settings: dict | None = None, seed: int | None = None,
              model: str = TTS_MODEL) -> tuple[bytes, dict]:
        body = {"text": text, "model_id": model}
        if settings:
            body["voice_settings"] = settings
        if seed is not None:
            body["seed"] = seed
        res = self._post(f"/text-to-speech/{voice_id}/with-timestamps", body, {"output_format": FORMAT})
        return base64.b64decode(res["audio_base64"]), res.get("alignment") or res.get("normalized_alignment") or {}

    def sfx(self, text: str, seconds: float | None, influence: float = 0.4, loop: bool = False) -> bytes:
        body = {"text": text, "model_id": SFX_MODEL, "prompt_influence": influence, "loop": loop}
        if seconds:
            body["duration_seconds"] = max(0.5, min(30.0, seconds))
        return self._post("/sound-generation", body, {"output_format": FORMAT}, raw=True)


def word_times(alignment: dict) -> list[dict]:
    """Character alignment -> [{"word", "start", "end"}], ignoring [audio tags] (they aren't spoken)."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words, cur, s, e, depth = [], "", None, None, 0
    for ch, a, b in zip(chars, starts, ends):
        if ch == "[":
            depth += 1
        if depth:
            if ch == "]":
                depth -= 1
            continue
        if re.match(r"[\w'*-]", ch):
            if not cur:
                s = a
            cur += ch
            e = b
        elif cur:
            words.append({"word": cur, "start": round(s, 3), "end": round(e, 3)})
            cur = ""
    if cur:
        words.append({"word": cur, "start": round(s, 3), "end": round(e, 3)})
    return words


def bleep_spans(words: list[dict], bleep: list[str], pad: float = 0.03) -> list[list[float]]:
    want = [re.sub(r"[^a-z]", "", w.lower()) for w in bleep]
    out = []
    for w in words:
        clean = re.sub(r"[^a-z]", "", w["word"].lower())
        if any(clean.startswith(x) for x in want if x):  # "fucking" also catches "fuckin"
            out.append([round(max(0.0, w["start"] - pad), 3), round(w["end"] + pad, 3)])
    return out


def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:10]


def _to_wav(mp3: bytes, dest: Path) -> Path:
    import subprocess
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".mp3")
    tmp.write_bytes(mp3)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-ar", "44100", "-ac", "2", str(dest)], check=True)
    tmp.unlink()
    return dest


def ensure_voices(p: Project, el: Eleven, cast: dict, preview_only: bool = False) -> dict:
    """Design and save each character's voice once. voices.json maps name -> voice_id."""
    vf = p.path("voices.json")
    saved = json.loads(vf.read_text()) if vf.exists() else {}
    for name, v in cast.get("voices", {}).items():
        if v.get("voice_id"):
            saved[name] = v["voice_id"]
            continue
        k = _key(v["description"], v.get("sample"), v.get("seed"))
        if saved.get(name) and saved.get(f"{name}.key") == k:
            continue
        print(f"  designing voice '{name}'...", file=sys.stderr)
        previews = el.design(v["description"], v["sample"], v.get("seed"))
        d = p.path("voices")
        for n, pv in enumerate(previews):
            _to_wav(base64.b64decode(pv["audio_base_64"]), d / f"{name}_preview{n}.wav")
        if preview_only:
            continue
        pick = previews[min(v.get("pick", 0), len(previews) - 1)]
        saved[name] = el.save_voice(f"songvid {p.slug} {name}", v["description"], pick["generated_voice_id"])
        saved[f"{name}.key"] = k
        p.write(vf, saved)
    return saved


def make_lines(p: Project, el: Eleven, cast: dict, voices: dict, only: list[str] | None = None) -> list[Path]:
    out = []
    for ln in cast.get("lines", []):
        if only and ln["id"] not in only:
            continue
        vid = voices[ln["voice"]]
        for t in range(ln.get("takes", 1)):
            k = _key(vid, ln["text"], ln.get("settings"), t, ln.get("model", TTS_MODEL))
            dest = p.path(f"vo/{ln['id']}_t{t}.wav")
            meta = dest.with_suffix(".json")
            if dest.exists() and meta.exists() and json.loads(meta.read_text()).get("key") == k:
                out.append(dest)
                continue
            print(f"  line {ln['id']} take {t}...", file=sys.stderr)
            audio, align = el.speak(vid, ln["text"], ln.get("settings"), seed=1000 + t, model=ln.get("model", TTS_MODEL))
            _to_wav(audio, dest)
            words = word_times(align)
            meta.write_text(json.dumps({"key": k, "text": ln["text"], "words": words,
                                        "bleeps": bleep_spans(words, ln.get("bleep_words", []))}, indent=1))
            out.append(dest)
    return out


def make_sfx(p: Project, el: Eleven, cast: dict, only: list[str] | None = None) -> list[Path]:
    out = []
    for fx in cast.get("sfx", []):
        if only and fx["id"] not in only:
            continue
        for t in range(fx.get("takes", 1)):
            k = _key(fx["text"], fx.get("seconds"), fx.get("influence"), fx.get("loop"), t)
            dest = p.path(f"sfx/{fx['id']}_t{t}.wav")
            meta = dest.with_suffix(".json")
            if dest.exists() and meta.exists() and json.loads(meta.read_text()).get("key") == k:
                out.append(dest)
                continue
            print(f"  sfx {fx['id']} take {t}...", file=sys.stderr)
            _to_wav(el.sfx(fx["text"], fx.get("seconds"), fx.get("influence", 0.4), fx.get("loop", False)), dest)
            meta.write_text(json.dumps({"key": k, "text": fx["text"]}))
            out.append(dest)
    return out


def run(p: Project, only: list[str] | None = None, preview_voices: bool = False) -> dict:
    cast = json.loads(p.path("cast.json").read_text())
    el = Eleven()
    voices = ensure_voices(p, el, cast, preview_only=preview_voices)
    if preview_voices:
        return {"voices": sorted(str(x) for x in p.path("voices").glob("*_preview*.wav"))}
    return {"lines": [str(x) for x in make_lines(p, el, cast, voices, only)],
            "sfx": [str(x) for x in make_sfx(p, el, cast, only)]}
