"""Stage 3: word-level timing for the lyrics (timing.json + lyrics.srt + lyrics.lrc).

Sources, best first:
  suno    - Suno's own aligned words (from the API, or a suno_aligned.json you dropped in)
  whisper - forced alignment of the known lyrics with stable-ts (pip install -e '.[align]')
  even    - energy-based guess; no extra deps, good enough to block out a storyboard
Every source is mapped onto the lyrics from song.json, so section tags and line breaks
stay intact no matter how the source tokenized things.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

import numpy as np

from ..lyrics import INLINE_TAG_RE, WORD_RE, LyricLine, norm, parse
from ..project import Project
from ..schemas import Analysis, Line, SongSpec, Timing, Word

Stamp = tuple[str, float, float]


# ---------- sources ----------

def from_suno(path: Path) -> list[Stamp]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("alignedWords") or data.get("aligned_words") or data.get("data", {}).get("alignedWords") or []
    stamps: list[Stamp] = []
    for w in data:
        if w.get("success") is False:
            continue
        start = w.get("startS", w.get("start_s"))
        end = w.get("endS", w.get("end_s"))
        if start is None or end is None:
            continue
        toks = WORD_RE.findall(INLINE_TAG_RE.sub(" ", w.get("word", "")))
        # One entry can hold several tokens ("[Chorus]\nhold on"); split its span evenly.
        step = (end - start) / max(1, len(toks))
        for i, t in enumerate(toks):
            stamps.append((t, start + i * step, start + (i + 1) * step))
    return stamps


# ---------- .lrc / .srt (e.g. the "Suno Lyric Downloader" extension) ----------

_LRC_TAG = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_LRC_WORD = re.compile(r"<(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)>")
_SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _secs(m: str, s: str) -> float:
    s = s.replace(":", ".", 1) if s.count(":") else s
    return int(m) * 60 + float(s)


def _words(text: str) -> list[str]:
    text = INLINE_TAG_RE.sub(" ", text)          # [Verse 1], [Chorus] ...
    text = re.sub(r"\((?:instrumental|music|intro|outro|break)[^)]*\)", " ", text, flags=re.I)
    return WORD_RE.findall(text)


def _spread(words: list[str], start: float, end: float) -> list[Stamp]:
    """Line-level timing: share the line's span between its words by length."""
    if not words:
        return []
    end = max(end, start + 0.12 * len(words))
    weights = [max(1.0, len(w) / 3) for w in words]
    total, pos, out = sum(weights), start, []
    for w, wt in zip(words, weights):
        d = (end - start) * wt / total
        out.append((w, pos, pos + d))
        pos += d
    return out


LineSpan = tuple[float, float, list[str]]  # (start, latest possible end, words)


def from_lrc(text: str) -> tuple[list[Stamp], str, list[LineSpan]]:
    """Plain LRC ([mm:ss.xx]line), enhanced LRC (<mm:ss.xx>word), repeated tags ([t1][t2]chorus)."""
    entries: list[tuple[float, str]] = []
    word_level: list[Stamp] = []
    for raw in text.splitlines():
        tags = list(_LRC_TAG.finditer(raw))
        if not tags:
            continue  # [ar:...] metadata and blank lines
        body = raw[tags[-1].end():]
        if _LRC_WORD.search(body):
            marks = list(_LRC_WORD.finditer(body))
            for i, mk in enumerate(marks):
                seg = body[mk.end(): marks[i + 1].start() if i + 1 < len(marks) else len(body)]
                ws = _words(seg)
                if not ws:
                    continue
                st = _secs(*mk.groups())
                en = _secs(*marks[i + 1].groups()) if i + 1 < len(marks) else st + 0.45 * len(ws)
                word_level += _spread(ws, st, en)
            continue
        for t in tags:
            entries.append((_secs(*t.groups()), body))
    if word_level:
        return sorted(word_level, key=lambda x: x[1]), "word", []
    entries.sort()
    spans: list[LineSpan] = []
    for i, (st, body) in enumerate(entries):
        ws = _words(body)
        if not ws:
            continue
        nxt = entries[i + 1][0] if i + 1 < len(entries) else st + 0.6 * len(ws) + 1.0
        spans.append((st, max(st + 0.2, nxt - 0.05), ws))
    return _rough(spans), "line", spans


def from_srt(text: str) -> tuple[list[Stamp], str, list[LineSpan]]:
    spans: list[LineSpan] = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        m = _SRT_TIME.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 10 ** len(m.group(4))
        en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 10 ** len(m.group(8))
        body = re.sub(r"<[^>]+>", "", block[m.end():])  # strip <i>, <font> styling
        ws = _words(body)
        if ws:
            spans.append((st, en, ws))
    n_words = sum(len(w) for _, _, w in spans)
    if spans and n_words / len(spans) <= 1.5:  # one word per cue: already word-level
        return [x for st, en, ws in spans for x in _spread(ws, st, en)], "word", []
    return _rough(spans), "line", spans


def _rough(spans: list[LineSpan]) -> list[Stamp]:
    """Audio-free fallback for line-level timing: a sung pace, not the whole gap to the next line."""
    out: list[Stamp] = []
    for st, limit, ws in spans:
        out += _spread(ws, st, min(limit, st + 0.25 * sum(syllables(w) for w in ws) + 0.2))
    return out


def syllables(word: str) -> int:
    """Rough sung-syllable count. Hyphen stretches ("li-ight") count as held notes."""
    parts = [p for p in word.lower().split("-") if p]
    n = 0
    for part in parts:
        groups = re.findall(r"[aeiouy]+", part)
        k = len(groups)
        if part.endswith("e") and k > 1 and not part.endswith(("le", "ee")):
            k -= 1
        n += max(1, k)
    return max(1, n)


def pace_lines(spans: list[LineSpan], audio: Path, tempo: float | None = None) -> list[Stamp]:
    """Place words inside line-level timing using the audio.

    Each line starts where the file says. Its sung length comes from its syllable count at the
    song's tempo (about an eighth note per syllable), stretched or cut to where vocal energy
    actually drops off, and each word start is snapped to the nearest vocal onset.
    """
    import librosa

    y, sr = librosa.load(str(audio), sr=22050, mono=True)
    hop = 256
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    S, _ = librosa.decompose.hpss(S)  # harmonic part only: drums and hats aren't syllables
    band = S[(freqs > 250) & (freqs < 3500)]
    env = np.convolve(band.mean(axis=0), np.ones(5) / 5, mode="same")
    t = librosa.frames_to_time(np.arange(len(env)), sr=sr, hop_length=hop)
    flux = np.maximum(0, np.diff(np.log1p(band * 20), axis=1, prepend=np.log1p(band[:, :1] * 20))).sum(0)
    peaks = librosa.onset.onset_detect(onset_envelope=flux, sr=sr, hop_length=hop, backtrack=False)
    on_t = librosa.frames_to_time(peaks, sr=sr, hop_length=hop)
    strength = flux[peaks]
    # Keep only attacks that aren't overshadowed by a stronger one just after them (releases,
    # breaths and pad swells tend to fire a weaker onset right before the real syllable).
    keep = [i for i in range(len(peaks))
            if not np.any((on_t > on_t[i]) & (on_t <= on_t[i] + 0.25) & (strength > strength[i]))]
    onsets = on_t[keep]
    if not tempo:
        tempo = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0]) or 110.0
    beat = 60.0 / tempo
    while beat > 0.75:  # half-time trackers: keep the beat in a singable range
        beat /= 2
    while beat < 0.3:
        beat *= 2
    syl = float(np.clip(beat / 2, 0.13, 0.32))

    out: list[Stamp] = []
    for st, limit, ws in spans:
        counts = [syllables(w) for w in ws]
        prior = sum(counts) * syl + 0.1
        lo, hi = st + 0.6 * prior, limit
        end = min(limit, st + 1.15 * prior)
        a, b = np.searchsorted(t, st), np.searchsorted(t, limit)
        if b - a > 8:
            seg = env[a:b]
            thr = np.percentile(seg, 30) + 0.15 * (np.percentile(seg, 90) - np.percentile(seg, 30))
            quiet = seg < thr
            need = max(2, int(0.35 / (hop / sr)))  # 350 ms of quiet ends the phrase; shorter is a word gap
            run = 0
            for i in range(len(seg)):
                run = run + 1 if quiet[i] else 0
                ti = t[a + i - run + 1]
                if run >= need and lo <= ti <= hi:
                    end = ti
                    break
        end = max(end, st + 0.12 * len(ws))
        words = _spread_weighted(ws, counts, st, end)
        # Match word starts to vocal onsets in order (first word keeps the file's line start).
        cand = [float(o) for o in onsets if st + 0.08 < o < end - 0.05]
        est = [x[1] for x in words[1:]]
        miss = max(0.3, 1.2 * syl, 0.6 * (end - st) / len(ws))  # stretched lines tolerate bigger moves
        snapped = _assign(est, cand, miss=miss) if est and cand else est
        fixed = [[words[0][0], st, words[0][2]]]
        for (w, _, we_), s0 in zip(words[1:], snapped):
            fixed.append([w, max(s0, fixed[-1][1] + 0.06), we_])
        for k in range(len(fixed)):
            fixed[k][2] = fixed[k + 1][1] if k + 1 < len(fixed) else max(end, fixed[k][1] + 0.08)
        out += [tuple(x) for x in fixed]
    return out


def _spread_weighted(words: list[str], weights: list[int], start: float, end: float) -> list[Stamp]:
    total, pos, out = sum(weights), start, []
    for w, wt in zip(words, weights):
        d = (end - start) * wt / total
        out.append((w, pos, pos + d))
        pos += d
    return out


def timed_lyrics_sources(p: Project) -> list[tuple[str, list[Stamp], str, list[LineSpan]]]:
    """Every timed-lyrics file in the project, finest first: (label, stamps, level, line spans)."""
    found = []
    if p.suno_aligned.exists():
        found.append(("Suno aligned words", from_suno(p.suno_aligned), "word", []))
    if p.suno_lrc.exists():
        st, lvl, spans = from_lrc(p.suno_lrc.read_text(errors="replace"))
        found.append((f"Suno .lrc ({lvl}-level)", st, lvl, spans))
    if p.suno_srt.exists():
        st, lvl, spans = from_srt(p.suno_srt.read_text(errors="replace"))
        found.append((f"Suno .srt ({lvl}-level)", st, lvl, spans))
    found = [f for f in found if f[1]]
    return sorted(found, key=lambda f: (f[2] != "word", -len(f[1])))


def lyrics_from_timed(p: Project) -> str | None:
    """Rebuild the song's lyrics from an imported timing file (what Suno actually sang).

    Keeps section tags like [Chorus] when the file has them; otherwise starts a new
    "[Part N]" section at each instrumental gap of 4 s or more.
    """
    entries: list[tuple[float, str]] = []
    if p.suno_lrc.exists():
        for raw in p.suno_lrc.read_text(errors="replace").splitlines():
            tags = list(_LRC_TAG.finditer(raw))
            if tags:
                body = _LRC_WORD.sub("", raw[tags[-1].end():]).strip()
                entries += [(_secs(*t.groups()), body) for t in tags if body]
    elif p.suno_srt.exists():
        for block in re.split(r"\n\s*\n", p.suno_srt.read_text(errors="replace").replace("\r", "")):
            m = _SRT_TIME.search(block)
            if m:
                g = [int(x) for x in m.groups()]
                st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 10 ** len(m.group(4))
                for ln in re.sub(r"<[^>]+>", "", block[m.end():]).splitlines():
                    if ln.strip():
                        entries.append((st, ln.strip()))
    elif p.suno_aligned.exists():
        data = json.loads(p.suno_aligned.read_text())
        data = data.get("alignedWords") or data.get("aligned_words") or [] if isinstance(data, dict) else data
        text, t0 = "", None
        for w in data:
            t0 = w.get("startS", w.get("start_s", 0)) if t0 is None else t0
            text += w.get("word", "")
            if "\n" in w.get("word", ""):
                for ln in text.splitlines():
                    if ln.strip():
                        entries.append((t0, ln.strip()))
                text, t0 = "", None
        if text.strip():
            entries.append((t0 or 0, text.strip()))
    entries.sort(key=lambda e: e[0])
    out: list[str] = []
    has_tags = any(re.match(r"^\s*\[[^\]]+\]", b) for _, b in entries)
    last_t, part = None, 0
    for t, body in entries:
        m = re.match(r"^\s*(\[[^\]]+\])\s*(.*)$", body)
        tag, rest = (m.group(1), m.group(2)) if m else (None, body)
        if not has_tags and (last_t is None or t - last_t >= 4.0):
            part += 1
            out.append(f"{chr(10) if out else ''}[Part {part}]")
        if tag:
            out.append(f"{chr(10) if out else ''}{tag}")
        if rest.strip():
            out.append(rest.strip())
            last_t = t
    text = "\n".join(out).strip()
    return text if parse(text) else None


def save_timed_lyrics(p: Project, src: Path, name: str | None = None) -> Path:
    """Store an imported .json / .lrc / .srt under its canonical project name."""
    name = (name or src.name).lower()
    head = src.read_text(errors="replace")[:4000]
    if name.endswith(".json") or head.lstrip().startswith(("{", "[{")):
        dest = p.suno_aligned
    elif name.endswith(".srt") or _SRT_TIME.search(head):
        dest = p.suno_srt
    elif name.endswith(".lrc") or _LRC_TAG.search(head):
        dest = p.suno_lrc
    else:
        raise ValueError("That doesn't look like an .lrc, .srt or Suno aligned-words .json file.")
    p.ensure()
    dest.write_text(src.read_text(errors="replace"))
    return dest


def from_whisper(audio: Path, lines: list[LyricLine], cfg: dict) -> list[Stamp]:
    try:
        import stable_whisper
    except ImportError as e:
        raise RuntimeError("Whisper alignment needs stable-ts: pip install -e '.[align]'") from e
    model = stable_whisper.load_model(cfg["align"]["whisper_model"])
    text = "\n".join(ln.text for ln in lines)
    kwargs = {"language": "en"}
    if cfg["align"].get("isolate_vocals"):
        kwargs["denoiser"] = "demucs"
    print(f"  aligning with whisper-{cfg['align']['whisper_model']} (slow on CPU)...", file=sys.stderr)
    result = model.align(str(audio), text, **kwargs)
    return [(w.word.strip(), float(w.start), float(w.end)) for w in result.all_words() if w.word.strip()]


def from_energy(audio: Path, lines: list[LyricLine]) -> tuple[list[Stamp], float]:
    """Spread words over the vocal-band-loud parts of the song. A rough guess."""
    import librosa

    y, sr = librosa.load(str(audio), sr=22050, mono=True)
    dur = len(y) / sr
    hop = 512
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    band = S[(freqs > 300) & (freqs < 3400)].mean(axis=0)
    band = np.convolve(band, np.ones(43) / 43, mode="same")  # ~1 s smoothing
    active = band > np.percentile(band, 50)
    t = librosa.frames_to_time(np.arange(len(band)), sr=sr, hop_length=hop)
    act_t = t[active] if active.any() else t
    lo, hi = float(act_t[0]), float(act_t[-1])
    # Cumulative "vocal time" so words land only in active regions.
    cum = np.cumsum(active.astype(float))
    total = cum[-1] or 1.0
    words = [(w, li) for li, ln in enumerate(lines) for w in ln.words]
    sizes = [max(1.0, len(w) / 3) for w, _ in words]  # rough syllable weight within a line

    def at(v: float) -> float:
        return float(np.interp(v, cum, t))

    # Expected line starts: sung lines tend to take about a bar or two each regardless of
    # word count, so weight lines nearly evenly over active time.
    lw = [1 + 0.1 * len(ln.words) for ln in lines]
    exp_start, pos = [], 0.0
    for w_ in lw:
        exp_start.append(at(pos))
        pos += total * w_ / sum(lw)
    # Snap each line, in order, onto a vocal region (rise of activity) near its expected start;
    # its words then fill that region up to where activity falls (or the next line starts).
    rises = t[1:][active[1:] & ~active[:-1]]
    falls = t[1:][~active[1:] & active[:-1]]
    starts = _assign(exp_start, list(rises))
    stamps = []
    for li in range(len(lines)):
        idx = [i for i, (_, l) in enumerate(words) if l == li]
        st = starts[li]
        nxt = starts[li + 1] if li + 1 < len(lines) else hi
        fall = falls[falls > st + 0.2]
        en = min(nxt - 0.05, float(fall[0]) if len(fall) else hi)
        if en - st < 0.15 * len(idx):
            en = min(nxt - 0.05, st + 0.35 * len(idx))
        tot = sum(sizes[i] for i in idx)
        cur = st
        for i in idx:
            d = (en - st) * sizes[i] / tot
            stamps.append((words[i][0], cur, cur + d))
            cur += d
    return stamps, dur


def _assign(expected: list[float], rises: list[float], miss: float = 4.0) -> list[float]:
    """Monotonic DP: give each line its own rise (in order), or leave it at its expected time
    at cost `miss`. Minimises total |rise - expected|."""
    L, R = len(expected), len(rises)
    INF = float("inf")
    # best[i][j]: cost of placing lines[:i] using rises[:j]; back pointers for reconstruction
    best = [[INF] * (R + 1) for _ in range(L + 1)]
    back = [[None] * (R + 1) for _ in range(L + 1)]
    best[0] = [0.0] * (R + 1)
    for i in range(1, L + 1):
        e = expected[i - 1]
        for j in range(R + 1):
            c = best[i - 1][j] + miss  # line i unsnapped
            if c < best[i][j]:
                best[i][j], back[i][j] = c, (j, None)
            if j > 0:
                c = best[i - 1][j - 1] + abs(rises[j - 1] - e)  # line i takes rise j
                if c < best[i][j]:
                    best[i][j], back[i][j] = c, (j - 1, rises[j - 1])
                if best[i][j - 1] < best[i][j]:  # skip rise j
                    best[i][j], back[i][j] = best[i][j - 1], ("skip", None)
    out, i, j = [0.0] * L, L, R
    while i > 0:
        pj, val = back[i][j]
        if pj == "skip":
            j -= 1
            continue
        out[i - 1] = val if val is not None else expected[i - 1]
        i, j = i - 1, pj
    for k in range(1, L):  # keep strictly increasing
        out[k] = max(out[k], out[k - 1] + 0.3)
    return out


def whisper_available() -> bool:
    try:
        import stable_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _refine_with_whisper(tm: Timing, lines: list[LyricLine], audio: Path, duration: float, cfg: dict) -> Timing:
    """Line starts from the imported file, word timing inside each line from Whisper.

    Lines where Whisper's start disagrees with the file by more than a second (it lost its place)
    keep the paced timing. Any Whisper failure keeps the paced timing for the whole song.
    """
    from ..progress import report

    report(None, f"Whisper ({cfg['align']['whisper_model']}) is listening for word timing; a few minutes on CPU")
    try:
        wtm = map_onto(lines, from_whisper(audio, lines, cfg), duration, "whisper")
    except Exception as e:
        print(f"  whisper refinement skipped: {e}", file=sys.stderr)
        return tm
    used = 0
    for li, (a, b) in enumerate(zip(tm.lines, wtm.lines)):
        b_words = [wtm.words[i] for i in b.words]
        if abs(b.start - a.start) > 1.0 or sum(w.confident for w in b_words) < 0.5 * len(b_words):
            continue
        delta = a.start - b.start  # the file's line start wins; Whisper's spacing inside the line
        nxt = tm.lines[li + 1].start if li + 1 < len(tm.lines) else duration
        for i, w in zip(a.words, b_words):
            s = round(w.start + delta, 3)
            tm.words[i].start = min(s, nxt - 0.05)
            tm.words[i].end = round(min(max(w.end + delta, s + 0.05), nxt - 0.02), 3)
            tm.words[i].confident = w.confident
        a.start, a.end = tm.words[a.words[0]].start, tm.words[a.words[-1]].end
        used += 1
    tm.source = tm.source.replace("paced to the vocals)", f"words by Whisper on {used}/{len(tm.lines)} lines)")
    print(f"  whisper refined {used}/{len(tm.lines)} lines", file=sys.stderr)
    return tm


# ---------- mapping ----------

def map_onto(lines: list[LyricLine], stamps: list[Stamp], duration: float, source: str) -> Timing:
    ours = [(w, li) for li, ln in enumerate(lines) for w in ln.words]
    a = [norm(w) for w, _ in ours]
    b = [norm(w) for w, _, _ in stamps]
    starts = [None] * len(ours)
    ends = [None] * len(ours)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            _, s, e = stamps[blk.b + k]
            starts[blk.a + k], ends[blk.a + k] = s, e
    confident = [s is not None for s in starts]
    _fill_gaps(starts, ends, [len(w) for w, _ in ours], duration)

    words = [Word(text=w, start=round(s, 3), end=round(max(e, s + 0.05), 3), line=li, confident=c)
             for (w, li), s, e, c in zip(ours, starts, ends, confident)]
    out_lines = []
    for li, ln in enumerate(lines):
        idx = [i for i, w in enumerate(words) if w.line == li]
        out_lines.append(Line(index=li, text=ln.text, section=ln.section,
                              start=words[idx[0]].start, end=words[idx[-1]].end, words=idx))
    matched = sum(confident) / max(1, len(confident))
    print(f"  {source}: matched {matched:.0%} of {len(words)} words", file=sys.stderr)
    return Timing(source=source, duration=round(duration, 3), words=words, lines=out_lines)


def _fill_gaps(starts: list, ends: list, sizes: list[int], duration: float) -> None:
    """Interpolate unmatched words between their matched neighbours, by length."""
    n = len(starts)
    i = 0
    while i < n:
        if starts[i] is not None:
            i += 1
            continue
        j = i
        while j < n and starts[j] is None:
            j += 1
        left = ends[i - 1] if i > 0 else 0.0
        right = starts[j] if j < n else min(duration, left + 0.4 * (j - i))
        span = max(0.0, right - left)
        total = sum(sizes[i:j]) or 1
        pos = left
        for k in range(i, j):
            d = span * sizes[k] / total
            starts[k], ends[k] = pos, pos + d
            pos += d
        i = j
    # enforce monotonic order
    for k in range(1, n):
        if starts[k] < starts[k - 1]:
            starts[k] = starts[k - 1]
        if ends[k] < starts[k]:
            ends[k] = starts[k]


# ---------- exports ----------

def _ts(t: float, sep: str = ",") -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}{sep}{int(round((s % 1) * 1000)) % 1000:03d}"


def write_exports(p: Project, tm: Timing) -> None:
    srt = []
    for i, ln in enumerate(tm.lines, 1):
        srt.append(f"{i}\n{_ts(ln.start)} --> {_ts(ln.end + 0.3)}\n{ln.text}\n")
    p.path("lyrics.srt").write_text("\n".join(srt))
    lrc = []
    for ln in tm.lines:
        mm, ss = divmod(ln.start, 60)
        words = " ".join(f"<{int(divmod(tm.words[i].start, 60)[0]):02d}:{divmod(tm.words[i].start, 60)[1]:05.2f}>"
                         f"{tm.words[i].text}" for i in ln.words)
        lrc.append(f"[{int(mm):02d}:{ss:05.2f}]{words}")
    p.path("lyrics.lrc").write_text("\n".join(lrc) + "\n")


# ---------- entry ----------

def run(p: Project, cfg: dict, method: str | None = None) -> Timing:
    import soundfile as sf

    spec = p.read(p.song, SongSpec)
    lines = parse(spec.lyrics)
    if not lines:
        # No lyrics written in the Song step: use what Suno sang, from the imported timing file.
        text = lyrics_from_timed(p)
        if not text:
            raise RuntimeError("This song has no lyrics yet. Paste them into the Song step, or import "
                               "Suno's .lrc / .srt for this take and the lyrics will be filled in from it.")
        from .song import save
        spec.lyrics = text
        save(p, spec)
        lines = parse(text)
        print(f"  filled in {len(lines)} lyric lines from the imported timing file", file=sys.stderr)
    audio = p.audio()
    try:
        duration = float(sf.info(str(audio)).duration)
    except Exception:
        import librosa
        duration = float(librosa.get_duration(path=str(audio)))

    method = method or cfg["align"]["method"]
    order = {"auto": ["suno", "whisper", "even"], "suno": ["suno"], "whisper": ["whisper"], "even": ["even"]}[method]
    last_err = None
    refine = False
    for m in order:
        try:
            if m == "suno":
                srcs = timed_lyrics_sources(p)
                if not srcs:
                    raise RuntimeError("no Suno timing (.json, .lrc or .srt) in the project")
                label, stamps, level, spans = srcs[0]
                if level == "line" and spans:
                    tempo = p.read(p.analysis, Analysis).tempo if p.analysis.exists() else None
                    stamps = pace_lines(spans, audio, tempo)
                    label = label.replace("line-level)", "line-level, paced to the vocals)")
                    refine = whisper_available() and method in ("auto", "suno")
                m = label
            elif m == "whisper":
                stamps = from_whisper(audio, lines, cfg)
            else:
                stamps, duration = from_energy(audio, lines)
            if not stamps:
                raise RuntimeError("source returned no words")
            tm = map_onto(lines, stamps, duration, m)
            if refine:
                tm = _refine_with_whisper(tm, lines, audio, duration, cfg)
            break
        except Exception as e:
            last_err = e
            print(f"  {m} alignment unavailable: {e}", file=sys.stderr)
    else:
        raise RuntimeError(f"All alignment methods failed: {last_err}")
    p.write(p.timing, tm)
    write_exports(p, tm)
    return tm
