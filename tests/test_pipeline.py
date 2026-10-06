import json
import subprocess

import pytest

from songvid import project as proj
from songvid.lyrics import parse
from songvid.schemas import Analysis, Shot, SongSpec, Storyboard, Timing


@pytest.fixture()
def demo(tmp_path, monkeypatch):
    monkeypatch.setattr(proj, "PROJECTS_DIR", tmp_path)
    from songvid.demo import build

    p = proj.Project("demo").ensure()
    build(p)
    return p


def test_parse_sections_and_stretched_words():
    lines = parse("[Verse 1 - quiet]\nPorch light, humming!\n\n[Chorus]\nHold the li-ight (oh)\n[End]")
    assert [l.section for l in lines] == ["Verse 1", "Chorus"]
    assert lines[0].words == ["Porch", "light", "humming"]
    assert lines[1].words == ["Hold", "the", "li-ight", "oh"]


def test_map_onto_handles_noise_and_gaps():
    from songvid.stages.align import map_onto

    lines = parse("[Verse]\none two three four\nfive six")
    # source misses "three", inserts junk, and splits nothing
    stamps = [("one", 1.0, 1.4), ("two", 1.5, 1.9), ("uh", 2.0, 2.1), ("four", 3.0, 3.4),
              ("five", 5.0, 5.4), ("six", 5.5, 6.0)]
    tm = map_onto(lines, stamps, 10.0, "test")
    by = {w.text: w for w in tm.words}
    assert by["one"].start == 1.0 and by["six"].end == 6.0
    assert not by["three"].confident and 1.9 <= by["three"].start <= 3.0
    assert [l.start for l in tm.lines] == [1.0, 5.0]
    starts = [w.start for w in tm.words]
    assert starts == sorted(starts)


def test_suno_aligned_format_with_tags(tmp_path):
    from songvid.stages.align import from_suno

    f = tmp_path / "a.json"
    f.write_text(json.dumps({"aligned_words": [
        {"word": "[Chorus]\nHold on ", "success": True, "start_s": 1.0, "end_s": 2.0},
        {"word": "tight", "success": False, "start_s": 2.0, "end_s": 2.5}]}))
    assert from_suno(f) == [("Hold", 1.0, 1.5), ("on", 1.5, 2.0)]


def test_import_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(proj, "PROJECTS_DIR", tmp_path)
    from songvid.stages.song import import_markdown

    md = """# Porch Light

**Style**
```
Ambient folk, 96 BPM. Soft male vocal.
```

**Exclude**
```
autotune, EDM
```

**Lyrics**
```
[Verse 1]
Porch light humming
```

Settings: v6, Variety 0, Style Influence 75, Weirdness 20, Max Mode off
"""
    spec = import_markdown(proj.Project("imp").ensure(), md)
    assert spec.title == "Porch Light"
    assert spec.exclude == "autotune, EDM"
    assert spec.settings.style_influence == 75 and spec.settings.weirdness == 20
    assert (tmp_path / "imp" / "suno_paste.md").exists()


def test_tidy_makes_contiguous_snapped_shots():
    from songvid.stages.storyboard import tidy

    an = Analysis(duration=30, fps=24, tempo=120, beats=[], downbeats=[0, 2, 4, 6, 8, 10, 20],
                  sections=[], features_file="")
    board = Storyboard(shots=[Shot(id="b", start=9.7, end=12), Shot(id="a", start=0.3, end=9),
                              Shot(id="c", start=9.9, end=30, palette=["nope"])])
    out = tidy(board, an).shots
    assert [(s.start, s.end) for s in out] == [(0.0, 10.0), (10.0, 30)]
    assert out[1].palette[0].startswith("#")


def test_demo_pipeline_with_mocked_claude(demo, monkeypatch):
    from songvid.stages import align, analyze, storyboard

    cfg = proj.load_config(demo)
    tm = align.run(demo, cfg)
    assert tm.source == "Suno aligned words" and all(w.confident for w in tm.words)
    an = analyze.run(demo, cfg)
    assert 90 < an.tempo < 100 and an.sections[0].label == "Intro"

    def fake_ask(system, user, schema, cfg, max_tokens=0):
        assert "Downbeat" in user and "Hold the li-ight" in user
        return {"concept": "c", "look": "l", "letterbox": True, "shots": [
            dict(id="x", start=0, end=30, section="Intro", description="d", source="procedural",
                 scene="smoke", palette=["#000000", "#ffffff"], intensity=0.5, speed=0.5,
                 image_prompt="", motion_prompt="", transition_in="cut", lyrics_overlay=True, punch=0.2),
            dict(id="y", start=34.6, end=65, section="Chorus", description="d", source="procedural",
                 scene="rays", palette=["#000000", "#ffcc88"], intensity=0.9, speed=0.5,
                 image_prompt="", motion_prompt="", transition_in="flash", lyrics_overlay=True, punch=0.2)]}

    monkeypatch.setattr(storyboard, "ask_json", fake_ask)
    board = storyboard.generate(demo, cfg, "internal")
    assert board.shots[1].start == 35.0  # snapped to the chorus downbeat
    assert board.shots[-1].end == an.duration


def test_even_alignment_is_plausible(demo):
    from songvid.stages import align

    demo.suno_aligned.unlink()
    tm = align.run(demo, proj.load_config(demo), "even")
    truth = [10.0, 15.0, 20.0, 25.0, 35.0, 40.0, 45.0, 50.0]
    err = [abs(l.start - t) for l, t in zip(tm.lines, truth)]
    assert max(err) < 1.0  # easy on synthetic audio; real mixes are much rougher


def test_render_slice(demo):
    from songvid.render.compositor import render, stills
    from songvid.stages import align, analyze, storyboard

    cfg = proj.load_config(demo)
    align.run(demo, cfg)
    an = analyze.run(demo, cfg)
    demo.write(demo.storyboard, storyboard.tidy(storyboard.heuristic(an), an))
    out = render(demo, preview=True, start=34, end=37, workers=2)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                "csv=p=0", str(out)], capture_output=True, text=True).stdout)
    assert abs(dur - 3.0) < 0.2
    assert stills(demo).exists()


LRC_LINES = """[ar:Joel]
[ti:Five and One]
[00:10.00][Verse 1]Porch light humming in the rain
[00:15.00]Your coat still hanging by the door
[00:35.00][01:35.00]Hold the li-ight
"""


def test_lrc_line_level_and_repeated_tags():
    from songvid.stages.align import from_lrc

    stamps, level, spans = from_lrc(LRC_LINES)
    assert level == "line" and len(spans) == 4
    assert [w for w, _, _ in stamps[:6]] == ["Porch", "light", "humming", "in", "the", "rain"]
    assert stamps[0][1] == 10.0 and stamps[5][2] < 13.0  # a sung pace, not the whole 5 s gap
    holds = [s for w, s, _ in stamps if w == "Hold"]
    assert holds == [35.0, 95.0]  # one line, sung twice


def test_lrc_enhanced_word_level():
    from songvid.stages.align import from_lrc

    stamps, level, _ = from_lrc("[00:35.00]<00:35.00>Hold <00:36.33>the <00:37.67>li-ight <00:39.50>\n")
    assert level == "word"
    assert [(w, round(s, 2)) for w, s, _ in stamps] == [("Hold", 35.0), ("the", 36.33), ("li-ight", 37.67)]


def test_srt_word_and_line_cues():
    from songvid.stages.align import from_srt

    words = "1\n00:00:10,000 --> 00:00:10,600\nPorch\n\n2\n00:00:10,670 --> 00:00:11,200\n<i>light</i>\n"
    st, level, _ = from_srt(words)
    assert level == "word" and st[1][0] == "light" and st[1][1] == pytest.approx(10.67)
    lines = "1\r\n00:00:10,000 --> 00:00:14,200\r\n[Verse 1]\r\nPorch light humming in the rain\r\n\r\n"
    st, level, spans = from_srt(lines)
    assert level == "line" and len(st) == 6 and spans[0][1] == pytest.approx(14.2)


def test_align_from_imported_lrc_beats_guessing(demo):
    from songvid.stages import align

    demo.suno_aligned.unlink()  # no Suno JSON; only the extension's .lrc
    truth = [10.0, 15.0, 20.0, 25.0, 35.0, 40.0, 45.0, 50.0]
    spec = SongSpec.model_validate_json(demo.song.read_text())
    lrc = "\n".join(f"[00:{t:05.2f}]{ln.text}" for ln, t in zip(parse(spec.lyrics), truth))
    lrc = lrc.replace("Porch light humming", "Porch light flickers")  # Suno sang a different word
    src = demo.dir / "Five and One.lrc"
    src.write_text(lrc)
    assert align.save_timed_lyrics(demo, src) == demo.suno_lrc
    tm = align.run(demo, proj.load_config(demo))  # auto: picks the imported timing
    assert tm.source == "Suno .lrc (line-level, paced to the vocals)"
    assert [l.start for l in tm.lines] == pytest.approx(truth, abs=0.01)
    assert sum(not w.confident for w in tm.words) == 1  # only the word Suno changed


def test_paced_line_timing_tracks_the_vocals(demo):
    """Line-level timing paced against the audio should land words far closer than spreading them."""
    import json as _json

    from songvid.stages.align import _spread, from_lrc, pace_lines

    truth = [(w["word"].strip(), w["startS"]) for w in _json.loads(demo.suno_aligned.read_text())["alignedWords"]]
    spec = SongSpec.model_validate_json(demo.song.read_text())
    starts = [10.0, 15.0, 20.0, 25.0, 35.0, 40.0, 45.0, 50.0]
    lrc = "\n".join(f"[00:{t:05.2f}]{ln.text}" for ln, t in zip(parse(spec.lyrics), starts))
    _, _, spans = from_lrc(lrc)
    paced = pace_lines(spans, demo.audio(), 96)
    naive = [x for st, lim, ws in spans for x in _spread(ws, st, lim)]
    err = lambda st: sum(abs(a[1] - b[1]) for a, b in zip(st, truth)) / len(truth)  # noqa: E731
    assert [w for w, _, _ in paced] == [w for w, _ in truth]
    assert err(paced) < 0.3 and err(paced) < err(naive) / 2, (err(paced), err(naive))


def test_whisper_refines_inside_lrc_lines(demo, monkeypatch):
    """Whisper's word spacing is used inside lines; the file's line starts win; lost lines are skipped."""
    import json as _json

    from songvid.stages import align

    truth = [(w["word"].strip(), w["startS"], w["endS"]) for w in _json.loads(demo.suno_aligned.read_text())["alignedWords"]]
    demo.suno_aligned.unlink()
    spec = SongSpec.model_validate_json(demo.song.read_text())
    starts = [10.0, 15.0, 20.0, 25.0, 35.0, 40.0, 45.0, 50.0]
    demo.suno_lrc.write_text("\n".join(f"[00:{t:05.2f}]{ln.text}" for ln, t in zip(parse(spec.lyrics), starts)))
    # fake Whisper: true word times, 0.3 s late, and completely lost on the last line
    fake = [(w, s + 0.3, e + 0.3) for w, s, e in truth[:-4]] + [(w, s + 5, e + 5) for w, s, e in truth[-4:]]
    monkeypatch.setattr(align, "whisper_available", lambda: True)
    monkeypatch.setattr(align, "from_whisper", lambda *a, **k: fake)
    tm = align.run(demo, proj.load_config(demo))
    assert "Whisper on 7/8 lines" in tm.source
    assert [l.start for l in tm.lines] == pytest.approx(starts, abs=0.01)
    words = [(w.text, w.start) for w in tm.words]
    assert words[1] == ("light", pytest.approx(10.67, abs=0.02))  # Whisper spacing, shifted to the line start
    assert words[-1][1] < 54  # last line kept the paced timing, not Whisper's lost 5 s offset


def test_whole_line_lyrics_mode(demo):
    import numpy as np

    from songvid.render.typography import LyricLayer, resolve_style
    from songvid.stages import align

    tm = align.run(demo, proj.load_config(demo))
    assert resolve_style("auto", "Suno .lrc (line-level, paced to the vocals)") == "lines"
    assert resolve_style("auto", "Suno .lrc (line-level, words by Whisper on 7/8 lines)") == "words"
    assert resolve_style("words", "Suno .lrc (line-level)") == "words"
    lines_layer = LyricLayer(tm, 640, 360, None, style="lines")
    words_layer = LyricLayer(tm, 640, 360, None, style="words")
    t = tm.lines[0].start + 0.15  # first word only just sung
    a = lines_layer.draw(np.zeros((360, 640, 3), np.float32), t)
    b = words_layer.draw(np.zeros((360, 640, 3), np.float32), t)
    assert a.sum() > b.sum() * 1.5  # whole line lit vs one word lit, rest dimmed


def test_lyrics_filled_from_lrc_when_song_has_none(demo):
    from songvid.stages import align
    from songvid.stages.song import save

    spec = SongSpec.model_validate_json(demo.song.read_text())
    real = parse(spec.lyrics)
    spec.lyrics = "[Verse 1]\n\n[Chorus]\n\n[End]"  # the empty template a new song starts with
    save(demo, spec)
    demo.suno_aligned.unlink()
    starts = [10.0, 15.0, 20.0, 25.0, 35.0, 40.0, 45.0, 50.0]
    demo.suno_lrc.write_text("[ti:Porch Light]\n" + "\n".join(f"[00:{t:05.2f}]{ln.text}" for ln, t in zip(real, starts)))
    tm = align.run(demo, proj.load_config(demo))
    assert [l.text for l in tm.lines] == [l.text for l in real]
    assert [l.start for l in tm.lines] == pytest.approx(starts, abs=0.01)
    lyr = SongSpec.model_validate_json(demo.song.read_text()).lyrics
    assert lyr.startswith("[Part 1]") and "[Part 2]" in lyr  # split at the 10 s instrumental gap
    # with section tags in the file, those win
    demo.suno_lrc.write_text("[00:10.00][Verse 1]Porch light humming in the rain\n[00:35.00][Chorus]Hold the li-ight\n")
    spec.lyrics = ""
    save(demo, spec)
    assert align.lyrics_from_timed(demo) == "[Verse 1]\nPorch light humming in the rain\n\n[Chorus]\nHold the li-ight"


def test_no_lyrics_and_no_timing_explains_itself(demo):
    from songvid.stages import align
    from songvid.stages.song import save

    spec = SongSpec.model_validate_json(demo.song.read_text())
    spec.lyrics = "[Verse 1]\n\n[End]"
    save(demo, spec)
    demo.suno_aligned.unlink()
    with pytest.raises(RuntimeError, match="no lyrics yet"):
        align.run(demo, proj.load_config(demo))


def test_overlays_letterbox_and_censor(demo):
    import numpy as np

    from songvid.render.compositor import Renderer
    from songvid.schemas import Overlay
    from songvid.stages import align, analyze, storyboard

    cfg = proj.load_config(demo)
    align.run(demo, cfg, method="even")
    an = analyze.run(demo, cfg)
    board = storyboard.tidy(storyboard.heuristic(an), an)
    tv = board.shots[1]
    tv.letterbox = False
    board.overlays = [
        Overlay(kind="chyron", start=tv.start, end=tv.end, text="Horse used elevator", sub="Staff stunned", style="update"),
        Overlay(kind="ticker", start=tv.start, end=tv.end, items=["Horse fires horse-catcher"]),
        Overlay(kind="bug", start=tv.start, end=tv.end, sub="St. Eligius Memorial"),
        Overlay(kind="vs", start=board.shots[2].start, end=board.shots[2].end, text="The Horse", text2="The Hippo"),
        Overlay(kind="card", start=board.shots[3].start, end=board.shots[3].end, text="The End", sub="after the bit"),
    ]
    demo.write(demo.storyboard, board)
    r = Renderer(demo, cfg, preview=True)
    fps = r.fps
    film = r.frame(int((board.shots[0].start + 1) * fps))
    news = r.frame(int((tv.start + 1.5) * fps))  # well past the bars sliding away
    assert film[:5].max() == 0 and news[:5].max() > 0  # letterboxed film shot vs full-frame TV shot
    clean = r.frame(int((tv.start + 1.5) * fps), overlays=False)
    assert np.abs(news.astype(int) - clean.astype(int)).mean() > 1  # the graphics actually drew
    for t in [board.shots[2].start + 1.0, board.shots[3].start + 1.0]:
        assert r.frame(int(t * fps)).shape == film.shape

    # a censored sung word becomes a black bar; uncensored renders keep it
    w = r.timing.words[3]
    board.censor = [w.text]
    demo.write(demo.storyboard, board)
    from songvid.render.typography import LyricLayer
    lay = LyricLayer(r.timing, 960, 540, None, censor={w.text})
    sp = [s for s in lay._layout(w.line) if s.start == w.start][0]
    assert sp.bar and not [s for s in LyricLayer(r.timing, 960, 540, None)._layout(w.line) if s.bar]


def test_video_models_and_spend_cap(demo, monkeypatch):
    from songvid.render import generate
    from songvid.schemas import Shot, Storyboard

    board = Storyboard(shots=[
        Shot(id="a", start=0, end=4, source="generated", motion_prompt="horse walks", video_model="veo-fast"),
        Shot(id="b", start=4, end=9, source="generated", motion_prompt="horse rears", video_model="seedance"),
    ])
    demo.write(demo.storyboard, board)
    demo.stills_dir.mkdir(parents=True, exist_ok=True)
    for sid in "ab":
        (demo.stills_dir / f"{sid}_key.png").write_bytes(b"png")
    generate.approve(demo, ["all"])
    (demo.dir / "songvid.yaml").write_text("generate:\n  spend_cap_usd: 3.0\n")
    monkeypatch.setenv("FAL_KEY", "test")
    calls = []

    def fake_run(self, model, args):
        calls.append((model, args))
        return {"video": {"url": "https://v3.fal.media/x.mp4"}}

    monkeypatch.setattr(generate.Fal, "run", fake_run)
    monkeypatch.setattr(generate.Fal, "download", lambda self, url, dest: dest)

    plan = generate.plan_animate(demo)
    assert [(s.id, d) for s, d in plan] == [("a", 4), ("b", 4)]  # 5 s shot fits a 4 s clip stretched 1.3x
    usd, unknown = generate.plan_cost(demo, plan)
    assert usd == pytest.approx(0.4 + 4 * 0.473) and not unknown

    with pytest.raises(RuntimeError, match="spend cap"):
        (demo.dir / "songvid.yaml").write_text("generate:\n  spend_cap_usd: 1.0\n")
        generate.animate(demo)
    assert not calls  # refused before submitting anything

    (demo.dir / "songvid.yaml").write_text("generate:\n  spend_cap_usd: 3.0\n")
    generate.animate(demo)
    by_model = {m: a for m, a in calls}
    veo = by_model["fal-ai/veo3.1/fast/image-to-video"]
    assert veo["duration"] == "4s" and veo["image_url"].startswith("data:image/png") and veo["generate_audio"] is False
    assert by_model["bytedance/seedance-2.5/image-to-video"]["duration"] == "4"
    assert generate.spent_usd(demo) == pytest.approx(usd)
    with pytest.raises(RuntimeError, match="spend cap"):  # a second round would cross $3
        (demo.clips_dir).mkdir(exist_ok=True)
        generate.animate(demo, ["a", "b"])


def test_mix_inserts_cuts_bleeps_and_ducking(demo):
    import numpy as np
    import soundfile as sf

    from songvid.stages import align, mix

    cfg = proj.load_config(demo)
    align.run(demo, cfg, method="even")
    tm = demo.read(demo.timing, Timing)
    w = tm.words[5]
    sr = mix.SR
    (demo.dir / "sfx").mkdir()
    sf.write(demo.path("sfx/voice.wav"), (0.2 * np.sin(2 * np.pi * 300 * np.arange(sr) / sr)).astype(np.float32), sr)
    demo.path("mix.json").write_text(json.dumps({
        "inserts": [{"id": "gap", "at": 20.0, "seconds": 2.0}],
        "cuts": [{"start": 40.0, "end": 41.5}],
        "bleeps": [{"word": w.text}],
        "cues": [{"file": "sfx/voice.wav", "insert": "gap", "offset": 0.5},
                 {"file": "sfx/voice.wav", "at": 30.0, "duck_db": -12}],
    }))
    res = mix.run(demo, cfg, use_stems=False)
    orig = mix.load(demo.path("audio_suno.wav"))
    assert res["duration"] == pytest.approx(len(orig) / sr + 2.0 - 1.5, abs=0.01)
    shifted = demo.read(demo.timing, Timing)
    for a, b in zip(tm.words, shifted.words):
        expect = a.start + (2.0 if a.start >= 20 else 0) - (1.5 if a.start >= 41.5 else 0)
        if not 40 <= a.start < 41.5:
            assert b.start == pytest.approx(expect, abs=0.002)

    cen, unc = mix.load(demo.path("audio.wav")), mix.load(demo.path("audio_uncensored.wav"))

    def band(x, s, e, hz):
        seg = x[int(s * sr):int(e * sr), 0]
        f = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        fr = np.fft.rfftfreq(len(seg), 1 / sr)
        return f[(fr > hz - 15) & (fr < hz + 15)].max() / (f.max() + 1e-9)

    ws = shifted.words[5]
    assert band(cen, ws.start, ws.end, 1000) > 0.9 > band(unc, ws.start, ws.end, 1000)
    assert band(unc, 20.6, 21.4, 300) > 0.9  # the cue plays inside the inserted gap
    # the music under the ducked cue is ~12 dB quieter than the same music without it
    plain = mix.build(demo, {"cues": []}, censored=False)[0]
    ducked = mix.build(demo, {"cues": [{"file": "sfx/voice.wav", "at": 30.0, "duck_db": -12}]}, censored=False)[0]
    a0, n = int(30.0 * sr), int(0.4 * sr)
    voice = mix.load(demo.path("sfx/voice.wav"))
    off = int(0.3 * sr)
    music = ducked[a0 + off:a0 + off + n] - voice[off:off + n]
    ratio = np.sqrt((music ** 2).mean() / (plain[a0 + off:a0 + off + n] ** 2).mean())
    assert 20 * np.log10(ratio) == pytest.approx(-12, abs=1.0)


def test_eleven_word_times_bleeps_and_cache(demo, monkeypatch):
    import base64

    import numpy as np

    from songvid import eleven
    from songvid.stages import mix

    text = "[shouting] I'm so fucking crazy!"
    chars = list(text)
    starts = [i * 0.05 for i in range(len(chars))]
    align = {"characters": chars, "character_start_times_seconds": starts,
             "character_end_times_seconds": [s + 0.05 for s in starts]}
    words = eleven.word_times(align)
    assert [w["word"] for w in words] == ["I'm", "so", "fucking", "crazy"]  # audio tags aren't words
    f = next(w for w in words if w["word"] == "fucking")
    assert eleven.bleep_spans(words, ["fucking"]) == [[round(f["start"] - 0.03, 3), round(f["end"] + 0.03, 3)]]

    # one silent mp3 stands in for every API response
    mp3 = demo.path("silence.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "2",
                    str(mp3)], check=True)
    calls = []

    class Fake:
        def design(self, d, s, seed=None):
            calls.append("design")
            return [{"generated_voice_id": "g1", "audio_base_64": base64.b64encode(mp3.read_bytes()).decode()}]

        def save_voice(self, name, d, g):
            calls.append("save")
            return "voice123"

        def speak(self, vid, text, settings=None, seed=None, model=None):
            calls.append(("speak", vid))
            return mp3.read_bytes(), align

        def sfx(self, text, seconds, influence=0.4, loop=False):
            calls.append("sfx")
            return mp3.read_bytes()

    monkeypatch.setattr(eleven, "Eleven", Fake)
    demo.path("cast.json").write_text(json.dumps({
        "voices": {"horse": {"description": "a horse", "sample": "x" * 120}},
        "lines": [{"id": "horse_1", "voice": "horse", "text": text, "takes": 2, "bleep_words": ["fucking"]}],
        "sfx": [{"id": "clop", "text": "hooves", "seconds": 2}],
    }))
    res = eleven.run(demo)
    assert len(res["lines"]) == 2 and len(res["sfx"]) == 1 and calls.count("design") == 1
    meta = json.loads(demo.path("vo/horse_1_t0.json").read_text())
    assert meta["bleeps"] and calls.count(("speak", "voice123")) == 2
    n = len(calls)
    eleven.run(demo)
    assert len(calls) == n  # everything cached: nothing re-generated, no credits spent

    for fx in ["phone", "pa", "tv", "hall"]:  # voice treatments run and keep the clip's start
        x = mix.load(demo.path("vo/horse_1_t0.wav"))
        assert len(mix.treat(x, fx)) >= len(x)
    assert np.isfinite(mix.treat(mix.load(demo.path("vo/horse_1_t0.wav")), None, pitch=-2)).all()


def test_gemini_keyframes_fall_back_between_api_shapes(demo, monkeypatch):
    import base64
    import io

    from PIL import Image

    from songvid.render import gemini, generate
    from songvid.schemas import Shot, Storyboard

    buf = io.BytesIO()
    Image.new("RGB", (64, 36), (200, 120, 40)).save(buf, "PNG")
    png = base64.b64encode(buf.getvalue()).decode()
    (demo.dir / "refs").mkdir()
    Image.new("RGB", (32, 32)).save(demo.path("refs/horse.png"))
    demo.write(demo.storyboard, Storyboard(look="prestige drama", shots=[
        Shot(id="s1", start=0, end=4, source="generated", image_prompt="the horse in an elevator", refs=["refs/horse.png"])]))
    (demo.dir / "songvid.yaml").write_text("generate:\n  image_provider: gemini\n")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    seen = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._body, self.ok, self.text = code, body, code == 200, str(body)

        def json(self):
            return self._body

    def post(self, url, json=None, timeout=None):
        seen.append((url, json))
        if url.endswith("/interactions"):
            return R(404, {"error": "not here"})
        return R(200, {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": png}}]}}]})

    monkeypatch.setattr(gemini.requests.Session, "post", post)
    out = generate.keyframes(demo)
    assert Image.open(out[0]).size == (64, 36)
    assert [u.rsplit("/", 1)[-1] for u, _ in seen] == ["interactions", "gemini-nano-banana-2.1:generateContent"]
    body = seen[1][1]
    assert body["contents"][0]["parts"][0]["text"].startswith("prestige drama, the horse")
    assert body["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"  # the reference went along
