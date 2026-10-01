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
    assert tm.source == "suno" and all(w.confident for w in tm.words)
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
