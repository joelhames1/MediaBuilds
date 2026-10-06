"""Post-production on the song: splice in inserts, lay dialogue and sound effects over a ducked band,
bleep words, and master. Driven by `mix.json` in the project.

All times in mix.json are in *source* time (the Suno song as imported), except cues placed inside an
insert, which say `"insert": "<id>", "offset": seconds`. The first `songvid mix` keeps the original
as audio_suno.* (plus timing_suno.json and analysis_suno.json) and writes the edited song as
audio.wav (bleeped) and audio_uncensored.wav, with timing.json shifted to match and analysis redone.

mix.json:
{
  "inserts": [{"id": "hooves", "at": 83.2, "seconds": 3.87, "fill": "silence"},
              {"id": "promo", "at": 140.0, "seconds": 7.74, "fill": "repeat", "from": 132.26}],
  "cuts":    [{"start": 120.0, "end": 124.0}],
  "mutes":   [{"stem": "vocals", "start": 132.2, "end": 147.7}],
  "bleeps":  [{"word": "fucking"}, {"start": 10.0, "end": 10.4}],
  "cues":    [{"file": "vo/hippo_1.wav", "insert": "promo", "offset": 0.3, "gain_db": 0, "duck_db": -9,
               "bleeps": [[1.2, 1.6]]},
              {"file": "sfx/ding.wav", "at": 61.5, "gain_db": -6}],
  "master":  {"lufs": -14, "true_peak": -1}
}
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from bisect import bisect_right
from pathlib import Path

import numpy as np
import soundfile as sf

from ..lyrics import norm
from ..project import Project
from ..schemas import Timing

SR = 44100
STEMS = ["vocals", "drums", "bass", "other"]
EDGE_FADE = 0.008  # seconds of fade either side of every splice, so joins never click (and never drift)
BLEEP_HZ = 1000.0
BLEEP_DB = -13.0


# ---------- audio io ----------

def load(path: Path, sr: int = SR) -> np.ndarray:
    """Any audio file -> float32 stereo (n, 2) at `sr`, via ffmpeg."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-acodec", "pcm_f32le",
                          "-ac", "2", "-ar", str(sr), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32).reshape(-1, 2).copy()


def db(x: float) -> float:
    return 10 ** (x / 20)


def ramp(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, max(1, n)))).astype(np.float32)


def fade_window(a: np.ndarray, start: int, end: int, gain: float, edge: int) -> None:
    """Multiply a[start:end] by gain, easing in and out over `edge` samples."""
    start, end = max(0, start), min(len(a), end)
    if end <= start:
        return
    env = np.full(end - start, gain, np.float32)
    e = min(edge, (end - start) // 2)
    if e > 0:
        r = ramp(e)
        env[:e] = 1 + (gain - 1) * r
        env[-e:] = 1 + (gain - 1) * r[::-1]
    a[start:end] *= env[:, None]


# ---------- time map ----------

class TimeMap:
    """Source (Suno) time <-> final time across inserts and cuts."""

    def __init__(self, inserts: list[dict], cuts: list[dict]):
        self.edits = sorted([(float(i["at"]), float(i["seconds"]), i["id"]) for i in inserts]
                            + [(float(c["start"]), -(float(c["end"]) - float(c["start"])), None) for c in cuts])
        self.insert_at = {}
        shift = 0.0
        for at, length, iid in self.edits:
            if iid is not None:
                self.insert_at[iid] = at + shift
            shift += length

    def __call__(self, t: float) -> float:
        out = t
        for at, length, _ in self.edits:
            if length >= 0 and t >= at:
                out += length
            elif length < 0:
                end = at - length
                if t >= end:
                    out += length
                elif t > at:
                    out -= t - at  # inside a cut: collapse to its start
        return out

    def to_dict(self) -> dict:
        return {"edits": [{"at": a, "seconds": l, "id": i} for a, l, i in self.edits], "inserts": self.insert_at}


# ---------- stems ----------

def separate(p: Project, source: Path, model: str = "htdemucs") -> Path:
    """Demucs stems for the source song into stems/<model>/<name>/ (cached)."""
    out = p.path("stems")
    d = out / model / source.stem
    if all((d / f"{s}.wav").exists() for s in STEMS):
        return d
    print(f"  separating stems with {model} (about real time on CPU)...", file=sys.stderr)
    subprocess.run([sys.executable, "-m", "demucs", "-n", model, "-o", str(out), str(source)], check=True)
    return d


# ---------- the mix ----------

def originals(p: Project) -> dict[str, Path]:
    """Keep the as-imported song, timing and analysis the first time we mix."""
    src = next((p.path(f"audio_suno{e}") for e in [".wav", ".mp3", ".m4a", ".flac"] if p.path(f"audio_suno{e}").exists()), None)
    if src is None:
        cur = p.audio()
        src = p.path(f"audio_suno{cur.suffix}")
        shutil.copy2(cur, src)
    out = {"audio": src}
    for name in ["timing", "analysis"]:
        keep = p.path(f"{name}_suno.json")
        if not keep.exists() and p.path(f"{name}.json").exists():
            shutil.copy2(p.path(f"{name}.json"), keep)
        out[name] = keep
    return out


def word_spans(timing: Timing, words: list[str], pad: float = 0.04) -> list[tuple[float, float]]:
    want = {norm(w) for w in words}
    return [(w.start - pad, w.end + pad) for w in timing.words if norm(w.text) in want]


def bleep_tone(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    tone = (np.sin(2 * np.pi * BLEEP_HZ * t) * db(BLEEP_DB)).astype(np.float32)
    e = min(int(0.006 * SR), n // 2)
    if e:
        tone[:e] *= ramp(e)
        tone[-e:] *= ramp(e)[::-1]
    return np.repeat(tone[:, None], 2, 1)


def build(p: Project, spec: dict, censored: bool = True, stems_dir: Path | None = None) -> tuple[np.ndarray, TimeMap]:
    src = originals(p)
    tm_src = Timing.model_validate_json(src["timing"].read_text()) if src["timing"].exists() else None
    edge = int(0.02 * SR)

    # 1. stems (or the whole song) in source time, with mutes and vocal bleeps
    if stems_dir is not None:
        stems = {s: load(stems_dir / f"{s}.wav") for s in STEMS}
    else:
        stems = {"mix": load(src["audio"])}
    n = max(len(a) for a in stems.values())
    for k in stems:
        stems[k] = np.pad(stems[k], ((0, n - len(stems[k])), (0, 0)))
    inserts = [{"id": f"ins{k}", **i} for k, i in enumerate(spec.get("inserts", []))]
    by_id = {i["id"]: i for i in inserts}
    for m in spec.get("mutes", []):
        target = stems.get(m.get("stem", "vocals"), stems.get("mix"))
        fade_window(target, int(m["start"] * SR), int(m["end"] * SR), db(m.get("gain_db", -120)), edge)

    spans: list[tuple[float, float]] = []
    for b in spec.get("bleeps", []):
        if "word" in b and tm_src:
            spans += word_spans(tm_src, [b["word"]], b.get("pad", 0.04))
        elif "start" in b:
            spans.append((float(b["start"]), float(b["end"])))
    tones = np.zeros((n, 2), np.float32)
    if censored:
        vox = stems.get("vocals", stems.get("mix"))  # with stems, only the singer drops out; the band plays on
        for s, e in spans:
            a, z = max(0, int(s * SR)), min(n, int(e * SR))
            if z > a:
                fade_window(vox, a, z, 0.0, int(0.005 * SR))
                tones[a:z] += bleep_tone(z - a)
    bed = sum(stems.values()) + tones

    # 2. inserts and cuts -> final timeline
    tmap = TimeMap(inserts, spec.get("cuts", []))
    pieces, cursor = [], 0
    for at, length, iid in tmap.edits:
        a = int(at * SR)
        if length >= 0:
            pieces.append(bed[cursor:a])
            ins = by_id[iid]
            L = int(length * SR)
            if ins.get("fill") == "repeat":
                f = int(float(ins["from"]) * SR)
                chunk = bed[f:f + L]
                pieces.append(np.pad(chunk, ((0, L - len(chunk)), (0, 0))))
            else:
                pieces.append(np.zeros((L, 2), np.float32))
            cursor = a
        else:
            pieces.append(bed[cursor:a])
            cursor = int((at - length) * SR)
    pieces.append(bed[cursor:])
    xf = int(EDGE_FADE * SR)
    for k, piece in enumerate(pieces):
        piece = pieces[k] = piece.copy()
        e = min(xf, len(piece) // 2)
        if e and k > 0:
            piece[:e] *= ramp(e)[:, None]
        if e and k < len(pieces) - 1:
            piece[-e:] *= ramp(e)[::-1, None]
    final = np.concatenate(pieces).astype(np.float32)

    # 3. cues over a ducked bed
    music_gain = np.ones(len(final), np.float32)
    layer = np.zeros_like(final)
    for c in spec.get("cues", []):
        audio = load(p.path(c["file"])) * db(c.get("gain_db", 0.0))
        if censored:
            for s, e in c.get("bleeps", []):
                a, z = max(0, int(s * SR)), min(len(audio), int(e * SR))
                if z > a:
                    fade_window(audio, a, z, 0.0, int(0.004 * SR))
                    audio[a:z] += bleep_tone(z - a)
        if c.get("pan"):
            pan = float(c["pan"])
            audio[:, 0] *= min(1.0, 1 - pan)
            audio[:, 1] *= min(1.0, 1 + pan)
        t0 = tmap.insert_at[c["insert"]] + c.get("offset", 0.0) if c.get("insert") else tmap(float(c["at"]))
        a = int(t0 * SR)
        z = a + len(audio)
        if z > len(final):  # a cue that runs past the end extends the song
            extra = z - len(final)
            final = np.pad(final, ((0, extra), (0, 0)))
            layer = np.pad(layer, ((0, extra), (0, 0)))
            music_gain = np.pad(music_gain, (0, extra), constant_values=1)
        layer[a:z] += audio
        if c.get("duck_db"):
            g = db(c["duck_db"])
            lo, hi = max(0, a - int(0.08 * SR)), min(len(final), z + int(0.25 * SR))
            seg = np.ones(hi - lo, np.float32)
            fade_window(seg[:, None], 0, hi - lo, g, int(0.12 * SR))
            music_gain[lo:hi] = np.minimum(music_gain[lo:hi], seg)
    final = final * music_gain[:, None] + layer
    return final, tmap


def master(raw: Path, out: Path, lufs: float = -14.0, true_peak: float = -1.0) -> Path:
    """Two-pass EBU R128 loudness normalisation with ffmpeg's loudnorm."""
    flt = f"loudnorm=I={lufs}:TP={true_peak}:LRA=11"
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(raw), "-af", flt + ":print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    m = json.loads(probe[probe.rfind("{"):probe.rfind("}") + 1])
    flt2 = (f"{flt}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af", flt2, "-ar", str(SR), str(out)], check=True)
    return out


def shift_timing(tm: Timing, tmap: TimeMap, duration: float) -> Timing:
    data = tm.model_dump()
    for w in data["words"]:
        w["start"], w["end"] = round(tmap(w["start"]), 3), round(tmap(w["end"]), 3)
    for ln in data["lines"]:
        ln["start"], ln["end"] = round(tmap(ln["start"]), 3), round(tmap(ln["end"]), 3)
    data["duration"] = round(duration, 3)
    data["source"] = tm.source + " (re-timed through mix.json)"
    return Timing.model_validate(data)


def run(p: Project, cfg: dict, use_stems: bool = True) -> dict:
    spec = json.loads(p.path("mix.json").read_text())
    src = originals(p)
    stems_dir = separate(p, src["audio"]) if use_stems else None
    outs = {}
    for censored, name in [(True, "audio.wav"), (False, "audio_uncensored.wav")]:
        final, tmap = build(p, spec, censored, stems_dir)
        raw = p.path(f".{name}.raw.wav")
        sf.write(raw, final, SR, subtype="FLOAT")
        mm = spec.get("master", {})
        outs[name] = master(raw, p.path(name), mm.get("lufs", -14.0), mm.get("true_peak", -1.0))
        raw.unlink()
    p.write(p.path("timemap.json"), tmap.to_dict())
    # stale formats of the song would shadow audio.wav in Project.audio()
    for ext in [".mp3", ".m4a", ".flac"]:
        if p.path(f"audio{ext}").exists():
            p.path(f"audio{ext}").unlink()
    dur = len(final) / SR
    if src["timing"].exists():
        p.write(p.timing, shift_timing(Timing.model_validate_json(src["timing"].read_text()), tmap, dur))
    from . import analyze
    analyze.run(p, cfg)
    return {"duration": dur, **{k: str(v) for k, v in outs.items()}}
