"""API keys: load from .env, report status, test, and save.

Keys live in the repo-root .env (gitignored, chmod 600) or in your shell environment.
A key already set in the shell wins over the .env file.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from .project import REPO_ROOT

ENV_FILE = Path(os.environ.get("SONGVID_ENV", REPO_ROOT / ".env"))

KEYS = {
    "ANTHROPIC_API_KEY": {
        "service": "Anthropic (Claude)",
        "unlocks": "Claude writes songs, directs the storyboard, and answers in the Claude panel.",
        "get": "https://platform.claude.com/settings/keys",
        "billing": "Pay-as-you-go API credits, separate from a Claude.ai subscription.",
        "prefix": "sk-ant-",
    },
    "FAL_KEY": {
        "service": "fal.ai",
        "unlocks": "AI keyframe stills and image-to-video clips (Flux, Kling, Veo and more).",
        "get": "https://fal.ai/dashboard/keys",
        "billing": "Prepaid credits, no subscription.",
        "prefix": "",
    },
    "ELEVENLABS_API_KEY": {
        "service": "ElevenLabs",
        "unlocks": "Character voices, spoken lines and sound effects mixed into the song (songvid voices).",
        "get": "https://elevenlabs.io/app/settings/api-keys",
        "billing": "Uses your ElevenLabs plan's credits.",
        "prefix": "sk_",
    },
    "GEMINI_API_KEY": {
        "service": "Google Gemini",
        "unlocks": "Keyframe stills with Nano Banana, using reference images to keep characters consistent.",
        "get": "https://aistudio.google.com/apikey",
        "billing": "Pay-as-you-go on a billed Google Cloud project.",
        "prefix": "",
    },
    "SUNOAPI_KEY": {
        "service": "Suno API provider (optional)",
        "unlocks": "Automatic Suno generation plus Suno's own word timing.",
        "get": "https://sunoapi.org/api-key",
        "billing": "Prepaid credits. Unofficial: songs are generated on the provider's accounts.",
        "prefix": "",
    },
}

_shell = {k for k in KEYS if os.environ.get(k)}  # set before we touched anything


def load_env() -> None:
    """Read .env into os.environ without overriding real shell variables."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in KEYS and k not in _shell:  # only our keys: a stray line can't redirect the SDKs or the browser
            os.environ[k] = v


def save_key(name: str, value: str) -> None:
    if name not in KEYS:
        raise ValueError(f"Unknown key {name}")
    value = value.strip()
    if any(c.isspace() or ord(c) < 32 for c in value) or len(value) > 500:
        raise ValueError("That doesn't look like an API key (it has spaces or line breaks in it).")
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else ["# songvid API keys. Never commit this file."]
    lines = [ln for ln in lines if not ln.strip().startswith(f"{name}=")]
    if value:
        lines.append(f"{name}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)


def status() -> list[dict]:
    out = []
    for name, meta in KEYS.items():
        val = os.environ.get(name, "")
        out.append({
            "name": name, **meta,
            "set": bool(val),
            "source": "shell" if name in _shell else (".env" if val else None),
            "hint": f"...{val[-4:]}" if len(val) > 14 else ("set" if val else ""),
        })
    return out


def test(name: str) -> tuple[bool, str]:
    """A cheap call that proves the key works without spending generation credits."""
    val = os.environ.get(name)
    if not val:
        return False, "Not set."
    try:
        if name == "ANTHROPIC_API_KEY":
            r = requests.get("https://api.anthropic.com/v1/models",
                             headers={"x-api-key": val, "anthropic-version": "2023-06-01"}, timeout=20)
            if r.ok:
                return True, "Key works. Claude models are reachable."
            return False, _why(r)
        if name == "FAL_KEY":
            # Status lookup of a made-up request: 401/403 means a bad key; 404/422 means auth passed.
            r = requests.get("https://queue.fal.run/fal-ai/flux/dev/requests/00000000-0000-0000-0000-000000000000/status",
                             headers={"Authorization": f"Key {val}"}, timeout=20)
            if r.status_code in (401, 403):
                return False, "fal.ai rejected this key."
            return True, "Key accepted by fal.ai."
        if name == "ELEVENLABS_API_KEY":
            r = requests.get("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": val}, timeout=20)
            if r.ok:
                d = r.json()
                left = (d.get("character_limit") or 0) - (d.get("character_count") or 0)
                return True, f"Key works. {d.get('tier', 'plan')} plan, {left:,} credits left this period."
            return False, _why(r)
        if name == "GEMINI_API_KEY":
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": val},
                             timeout=20)
            if r.ok:
                imgs = [m["name"].split("/")[-1] for m in r.json().get("models", []) if "image" in m["name"]]
                return True, "Key works." + (f" Image models: {', '.join(imgs[:4])}." if imgs else "")
            return False, _why(r)
        if name == "SUNOAPI_KEY":
            r = requests.get("https://api.sunoapi.org/api/v1/generate/credit",
                             headers={"Authorization": f"Bearer {val}"}, timeout=20)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.ok and body.get("code") == 200:
                return True, f"Key works. Credits remaining: {body.get('data')}."
            return False, body.get("msg") or _why(r)
    except requests.RequestException as e:
        return False, f"Could not reach the service: {e.__class__.__name__}."
    return False, "Unknown key."


def _why(r: requests.Response) -> str:
    if r.status_code in (401, 403):
        return "The service rejected this key."
    return f"Unexpected response ({r.status_code})."
