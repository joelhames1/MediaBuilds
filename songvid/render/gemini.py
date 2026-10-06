"""Gemini image generation (Nano Banana) with reference images, for keyframes that keep recurring
characters consistent from shot to shot.

Google has two REST shapes for this: the newer Interactions API and the classic generateContent.
We try the configured one first and fall back to the other, and find the image in either response.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta"


class Gemini:
    def __init__(self, model: str, api: str = "interactions"):
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("Set GEMINI_API_KEY (https://aistudio.google.com/apikey).")
        self.model, self.api = model, api
        self.s = requests.Session()
        self.s.headers.update({"x-goog-api-key": key, "Content-Type": "application/json"})

    def image(self, prompt: str, refs: list[Path] | None = None, aspect: str = "16:9", size: str = "2K") -> bytes:
        refs = refs or []
        errors = []
        order = ["interactions", "generate"] if self.api == "interactions" else ["generate", "interactions"]
        for api in order:
            try:
                return getattr(self, f"_{api}")(prompt, refs, aspect, size)
            except _ShapeError as e:
                errors.append(f"{api}: {e}")
        raise RuntimeError("Gemini returned no image. " + " | ".join(errors))

    def _interactions(self, prompt, refs, aspect, size) -> bytes:
        inp = [{"type": "text", "text": prompt}] + [
            {"type": "image", "mime_type": _mime(r), "data": base64.b64encode(r.read_bytes()).decode()} for r in refs]
        body = {"model": self.model, "input": inp,
                "response_format": {"type": "image", "aspect_ratio": aspect, "image_size": size}}
        return self._call(f"{BASE}/interactions", body)

    def _generate(self, prompt, refs, aspect, size) -> bytes:
        parts = [{"text": prompt}] + [
            {"inline_data": {"mime_type": _mime(r), "data": base64.b64encode(r.read_bytes()).decode()}} for r in refs]
        body = {"contents": [{"parts": parts}],
                "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect, "imageSize": size}}}
        return self._call(f"{BASE}/models/{self.model}:generateContent", body)

    def _call(self, url: str, body: dict) -> bytes:
        r = self.s.post(url, json=body, timeout=300)
        if r.status_code in (400, 404):
            raise _ShapeError(f"{r.status_code} {r.text[:240]}")
        if not r.ok:
            raise RuntimeError(f"Gemini failed ({r.status_code}): {r.text[:300]}")
        data = find_image(r.json())
        if data is None:
            raise _ShapeError(f"no image in response: {str(r.json())[:240]}")
        return base64.b64decode(data)


class _ShapeError(Exception):
    """This request shape isn't what the endpoint wants (or it answered without an image)."""


def _mime(p: Path) -> str:
    return mimetypes.guess_type(p.name)[0] or "image/png"


def find_image(obj) -> str | None:
    """First base64 image anywhere in a response, whichever API shape produced it."""
    if isinstance(obj, dict):
        mime = obj.get("mime_type") or obj.get("mimeType") or ""
        data = obj.get("data")
        if isinstance(data, str) and (mime.startswith("image") or (not mime and len(data) > 200)):
            return data
        for k in ["output_image", "inlineData", "inline_data", "image"]:
            if k in obj and (hit := find_image(obj[k])):
                return hit
        for v in obj.values():
            if isinstance(v, (dict, list)) and (hit := find_image(v)):
                return hit
    elif isinstance(obj, list):
        for v in obj:
            if hit := find_image(v):
                return hit
    return None
