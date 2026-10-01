"""Stage 4: audio analysis (analysis.json + features.npz at video fps)."""

from __future__ import annotations

import numpy as np

from ..lyrics import section_kind
from ..project import Project, load_config
from ..schemas import Analysis, Section, Timing


def _norm(x: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(x, 2), np.percentile(x, 98)
    return np.clip((x - lo) / (hi - lo + 1e-9), 0, 1)


def _smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return x
    k = np.hanning(n * 2 + 1)
    return np.convolve(x, k / k.sum(), mode="same")


def run(p: Project, cfg: dict | None = None) -> Analysis:
    import librosa

    cfg = cfg or load_config(p)
    fps = cfg["video"]["fps"]
    sr = 22050
    y, _ = librosa.load(str(p.audio()), sr=sr, mono=True)
    duration = len(y) / sr

    # Exact per-video-frame hop (avoids the drift a fixed integer hop accumulates).
    n_frames = int(np.ceil(duration * fps))
    centers = (np.arange(n_frames) + 0.5) / fps
    hop = 512
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    t_stft = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=hop)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)

    rms = librosa.feature.rms(S=S)[0]
    low = S[(freqs >= 40) & (freqs <= 200)].mean(axis=0)
    high = S[freqs >= 4000].mean(axis=0)
    centroid = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
    onset = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S, ref=np.max), sr=sr)

    def at_frames(x):
        return np.interp(centers, t_stft[: len(x)], x)

    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop)
    beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)
    tempo = float(np.atleast_1d(tempo)[0])
    # Downbeats: pick the beat phase (of 4) with the most low-end energy.
    if len(beats) >= 8:
        lo_at_beats = np.interp(beats, t_stft[: len(low)], low)
        phase = int(np.argmax([lo_at_beats[k::4].mean() for k in range(4)]))
        downbeats = beats[phase::4]
    else:
        downbeats = beats

    # Beat envelope: 1.0 on each beat, decaying over ~1/3 of a beat.
    beat_env = np.zeros(n_frames)
    decay = max(1.0, fps * 60 / max(tempo, 1) / 3)
    for b in beats:
        i = int(b * fps)
        if i < n_frames:
            seg = np.arange(n_frames - i)
            beat_env[i:] = np.maximum(beat_env[i:], np.exp(-seg / decay))

    feats = {
        "rms": _smooth(_norm(at_frames(rms)), 2),
        "low": _smooth(_norm(at_frames(low)), 1),
        "high": _smooth(_norm(at_frames(high)), 1),
        "onset": _norm(at_frames(onset)),
        "centroid": _smooth(_norm(at_frames(centroid)), 6),
        "beat": beat_env,
        "energy": _smooth(_norm(at_frames(rms)), int(fps * 2)),  # slow, for section dynamics
    }
    # Distance travelled, for camera fly-throughs that speed up with the bass.
    feats["travel"] = np.cumsum(0.4 + feats["low"] * 1.2 + feats["rms"] * 0.4) / fps
    np.savez_compressed(p.features, fps=fps, **feats)

    sections = _sections(p, duration, feats["energy"], fps)
    an = Analysis(duration=round(duration, 3), fps=fps, tempo=round(tempo, 2),
                  beats=[round(float(b), 3) for b in beats],
                  downbeats=[round(float(b), 3) for b in downbeats],
                  sections=sections, features_file=p.features.name)
    p.write(p.analysis, an)
    return an


def _sections(p: Project, duration: float, energy: np.ndarray, fps: int) -> list[Section]:
    """Sections from the timed lyrics (+ intro/outro/instrumental gaps)."""
    spans: list[tuple[str, float, float]] = []
    if p.timing.exists():
        tm = p.read(p.timing, Timing)
        for ln in tm.lines:
            if spans and spans[-1][0] == ln.section:
                spans[-1] = (ln.section, spans[-1][1], ln.end)
            else:
                spans.append((ln.section, ln.start, ln.end))
    out: list[tuple[str, float, float]] = []
    cursor = 0.0
    for label, s, e in spans:
        if s - cursor > 4.0:
            out.append(("Intro" if not out else "Instrumental", cursor, s))
        elif out:  # small gap: let the previous section run up to this one
            out[-1] = (out[-1][0], out[-1][1], s)
        else:  # short lead-in belongs to the first section
            s = 0.0
        out.append((label, s, e))
        cursor = e
    if not out:
        out.append(("Song", 0.0, duration))
    elif duration - cursor > 4.0:
        out.append(("Outro", cursor, duration))
    else:
        out[-1] = (out[-1][0], out[-1][1], duration)

    res = []
    for label, s, e in out:
        a, b = int(s * fps), max(int(s * fps) + 1, int(e * fps))
        en = float(energy[a:b].mean()) if a < len(energy) else 0.0
        res.append(Section(label=label, kind=section_kind(label), start=round(s, 3), end=round(e, 3),
                           energy=round(en, 3)))
    return res
