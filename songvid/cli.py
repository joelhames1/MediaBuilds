"""songvid CLI. Run `songvid <command> -h` for options."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .project import PROJECTS_DIR, Project, load_config


def _p(slug: str) -> Project:
    return Project(slug)


def cmd_song(a):
    from .llm import LLMUnavailable
    from .stages import song

    p = _p(a.slug).ensure()
    cfg = load_config(p)
    if a.refresh:
        from .schemas import SongSpec
        song.save(p, p.read(p.song, SongSpec))
    elif a.import_md:
        spec = song.import_markdown(p, Path(a.import_md).read_text())
    elif a.template:
        spec = song.template(p, a.brief or "")
    else:
        brief = a.brief or (p.path("brief.md").read_text() if p.path("brief.md").exists() else None)
        if not brief and not a.revise:
            sys.exit("Give a --brief (or --import a markdown draft, or --template).")
        skill = song.find_skill(cfg)
        print(f"  songwriter prompt: {skill or 'bundled fallback (suno skill not found)'}", file=sys.stderr)
        try:
            spec = song.generate(p, brief or "", cfg, revise=a.revise)
        except LLMUnavailable as e:
            sys.exit(f"{e}\nNo API access? Write the song in a Claude chat with your Suno skill, save the "
                     f"reply as draft.md, then: songvid song {a.slug} --import draft.md")
    print(p.path("suno_paste.md").read_text())
    print(f"\nSaved {p.song}\nPaste-ready blocks: {p.path('suno_paste.md')}")
    print(f"Next: songvid suno {a.slug} --import <downloaded.mp3>   (or --mode api)")


def cmd_suno(a):
    from .stages import suno

    p = _p(a.slug).ensure()
    cfg = load_config(p)
    if a.import_audio:
        suno.import_audio(p, Path(a.import_audio), Path(a.aligned) if a.aligned else None, a.url)
        print(f"Imported audio into {p.dir}")
    elif a.pick:
        from .schemas import SunoResult
        res = p.read(p.suno, SunoResult)
        suno.choose(p, res, res.clips[int(a.pick) - 1].id if a.pick.isdigit() else a.pick)
        print(f"Using take {a.pick}")
    elif (a.mode or cfg["suno"]["mode"]) == "api":
        res = suno.run_api(p, cfg)
        for i, c in enumerate(res.clips, 1):
            print(f"  take {i}: {c.local_path}  {c.duration or '?'} s  {c.audio_url}")
        print(f"Using take 1. Audition both; switch with: songvid suno {a.slug} --pick 2")
    else:
        print((p.path("suno_paste.md")).read_text() if p.path("suno_paste.md").exists() else "")
        print(f"""
Manual Suno steps:
  1. Paste the blocks above into suno.com Create (Custom). Set the sliders as listed.
  2. Generate 3-5 takes, audition in the browser, download only the keeper.
  3. songvid suno {a.slug} --import ~/Downloads/<song>.mp3 [--url <suno song url>]
     Optional: if you have Suno's aligned-words JSON for that clip, add --aligned file.json
""")
        return
    print(f"Next: songvid prep {a.slug}")


def cmd_align(a):
    from .stages import align

    p = _p(a.slug)
    method = a.method
    for f in getattr(a, "from_files", None) or []:
        print(f"  using timed lyrics from {align.save_timed_lyrics(p, Path(f)).name}")
        method = method or "suno"
    tm = align.run(p, load_config(p), method)
    low = sum(not w.confident for w in tm.words)
    print(f"Timed {len(tm.words)} words / {len(tm.lines)} lines via {tm.source}"
          + (f" ({low} interpolated)" if low else ""))
    print(f"Check it: play the audio with {p.path('lyrics.srt')} or {p.path('lyrics.lrc')}")


def cmd_analyze(a):
    from .stages import analyze

    an = analyze.run(_p(a.slug))
    print(f"{an.duration:.1f} s, {an.tempo:.0f} BPM, {len(an.beats)} beats")
    for s in an.sections:
        print(f"  {s.start:6.1f}-{s.end:6.1f}  {s.label:<14} energy {s.energy:.2f}")


def cmd_storyboard(a):
    from .stages import storyboard

    p = _p(a.slug)
    cfg = load_config(p)
    if a.heuristic:
        from .schemas import Analysis
        board = storyboard.tidy(storyboard.heuristic(p.read(p.analysis, Analysis), a.mode),
                                p.read(p.analysis, Analysis))
        p.write(p.storyboard, board)
    else:
        board = storyboard.generate(p, cfg, a.mode, a.direction or "")
    print(f"Concept: {board.concept}\nLook: {board.look}")
    for s in board.shots:
        src = "GEN" if s.source == "generated" else s.scene
        print(f"  {s.id} {s.start:6.1f}-{s.end:6.1f} {s.section:<12} {src:<9} {s.transition_in:<5} "
              f"{'L' if s.lyrics_overlay else '-'}  {s.description[:70]}")
    print(f"Edit {p.storyboard} freely, then: songvid stills {a.slug}")


def cmd_prep(a):
    """align + analyze + storyboard + stills in one go."""
    cmd_align(argparse.Namespace(slug=a.slug, method=None))
    cmd_analyze(a)
    cmd_storyboard(argparse.Namespace(slug=a.slug, mode=a.mode, direction=a.direction, heuristic=a.heuristic))
    cmd_stills(a)


def cmd_stills(a):
    from .render.compositor import stills

    out = stills(_p(a.slug))
    print(f"Contact sheet: {out}\nHappy? songvid render {a.slug} --preview --start 30 --end 60")


def cmd_keyframes(a):
    from .render.generate import keyframes

    p = _p(a.slug)
    outs = keyframes(p, a.shots or None, redo=a.redo)
    print(f"{len(outs)} keyframes in {p.stills_dir}. Refresh the sheet with: songvid stills {a.slug}")
    print(f"Approve: songvid approve {a.slug} all   (or list shot ids; --reject --note '...' to redo)")


def cmd_approve(a):
    from .render.generate import approve

    ap = approve(_p(a.slug), a.shots, value=not a.reject, note=a.note or "")
    print(json.dumps(ap, indent=1))


def cmd_animate(a):
    from .project import load_config
    from .render.generate import animate, plan_animate, plan_cost, spent_usd, video_profile

    p = _p(a.slug)
    plan = plan_animate(p, a.shots or None)
    if not plan:
        print("Nothing to animate (approve keyframes first, or clips already exist).")
        return
    g = load_config(p)["generate"]
    total = sum(d for _, d in plan)
    usd, unknown = plan_cost(p, plan)
    print(f"Will generate {len(plan)} clip(s), {total} s of video, about ${usd:.2f}"
          + (" plus models with no price in config" if unknown else "") + ":")
    for sh, d in plan:
        print(f"  {sh.id} {d}s on {video_profile(g, sh.video_model)['name']}  {sh.motion_prompt[:70]}")
    cap = g.get("spend_cap_usd")
    if cap is not None:
        print(f"Spent so far: ${spent_usd(p):.2f} of the ${cap:.2f} cap.")
    if not a.yes:
        print("This costs real money on fal.ai. Re-run with --yes to go.")
        return
    outs = animate(p, a.shots or None)
    print(f"{len(outs)} clips in {p.clips_dir}")


def cmd_voices(a):
    from . import eleven

    p = _p(a.slug)
    res = eleven.run(p, a.ids or None, preview_voices=a.preview)
    for k, v in res.items():
        print(f"{k}: {len(v)}")
        for x in v:
            print(f"  {x}")


def cmd_mix(a):
    from .project import load_config
    from .stages import mix

    p = _p(a.slug)
    res = mix.run(p, load_config(p), use_stems=not a.no_stems)
    print(f"Mixed {res['duration']:.1f} s: {p.path('audio.wav')} (bleeped) and {p.path('audio_uncensored.wav')}")
    print("timing.json is re-timed and analysis.json redone; the originals are kept as *_suno.*")


def cmd_render(a):
    from .render.compositor import render

    p = _p(a.slug)
    audio = p.path("audio_uncensored.wav") if a.uncensored and p.path("audio_uncensored.wav").exists() else None
    out = render(p, preview=a.preview, start=a.start, end=a.end, name=a.name, workers=a.workers,
                 uncensored=a.uncensored, audio=audio)
    print(out)


def cmd_demo(a):
    from .demo import build

    p = _p(a.slug).ensure()
    build(p)
    print(f"Demo song written to {p.dir}. Next: songvid prep {a.slug} --heuristic && songvid render {a.slug} --preview")


def cmd_status(a):
    p = _p(a.slug)
    checks = [("song.json", p.song), ("audio", None), ("timing.json", p.timing), ("analysis.json", p.analysis),
              ("storyboard.json", p.storyboard), ("contact sheet", p.stills_dir / "contact_sheet.jpg")]
    for name, path in checks:
        if path is None:
            try:
                ok = bool(p.audio())
            except FileNotFoundError:
                ok = False
        else:
            ok = path.exists()
        print(f"  [{'x' if ok else ' '}] {name}")
    if p.renders_dir.exists():
        for r in sorted(p.renders_dir.glob("*.mp4")):
            print(f"  render: {r}")


def cmd_ui(a):
    from .ui.server import serve

    serve(port=a.port, open_browser=not a.no_browser)


def cmd_list(a):
    for d in sorted(PROJECTS_DIR.glob("*/")):
        print(d.name)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="songvid", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(fn=fn)
        if name not in ("list", "ui"):
            s.add_argument("slug")
        return s

    s = add("song", cmd_song, "write lyrics + style (Claude), or import/template")
    s.add_argument("--brief")
    s.add_argument("--revise", help="feedback for a revision of the current song.json")
    s.add_argument("--import", dest="import_md", help="markdown with Style/Exclude/Lyrics code blocks")
    s.add_argument("--template", action="store_true")
    s.add_argument("--refresh", action="store_true", help="re-validate a hand-edited song.json, rebuild suno_paste.md")

    s = add("suno", cmd_suno, "make the song (manual steps, or third-party API)")
    s.add_argument("--mode", choices=["manual", "api"])
    s.add_argument("--import", dest="import_audio", help="downloaded Suno mp3/wav")
    s.add_argument("--aligned", help="timed lyrics for this take: .lrc, .srt or Suno aligned-words .json (optional)")
    s.add_argument("--url", help="Suno song URL, for your records")
    s.add_argument("--pick", help="switch to take N (api mode)")

    s = add("align", cmd_align, "word-level lyric timing")
    s.add_argument("--method", choices=["auto", "suno", "whisper", "even"])
    s.add_argument("--from", dest="from_files", nargs="+", metavar="FILE",
                   help="timed lyrics to align from: .lrc, .srt or Suno aligned-words .json")

    add("analyze", cmd_analyze, "beats, sections, energy curves")

    for name, fn, h in [("storyboard", cmd_storyboard, "shot list (Claude director, or --heuristic)"),
                        ("prep", cmd_prep, "align + analyze + storyboard + stills")]:
        s = add(name, fn, h)
        s.add_argument("--mode", choices=["internal", "external", "hybrid", "claude"], default="internal")
        s.add_argument("--direction", help="creative direction for the director")
        s.add_argument("--heuristic", action="store_true", help="skip Claude")

    add("stills", cmd_stills, "one frame per shot + contact sheet")

    s = add("keyframes", cmd_keyframes, "generate AI stills for 'generated' shots (fal.ai)")
    s.add_argument("shots", nargs="*")
    s.add_argument("--redo", action="store_true")

    s = add("approve", cmd_approve, "approve (or --reject) keyframes before animating")
    s.add_argument("shots", nargs="+", help="shot ids or 'all'")
    s.add_argument("--reject", action="store_true")
    s.add_argument("--note", help="what to change; appended to the prompt on --redo")

    s = add("animate", cmd_animate, "image-to-video for approved shots (fal.ai, costs money)")
    s.add_argument("shots", nargs="*")
    s.add_argument("--yes", action="store_true")

    s = add("voices", cmd_voices, "character voices, spoken lines and sound effects from cast.json (ElevenLabs)")
    s.add_argument("ids", nargs="*", help="only these line / sfx ids")
    s.add_argument("--preview", action="store_true", help="design voices and save previews without keeping any")

    s = add("mix", cmd_mix, "edit the song per mix.json: inserts, dialogue and SFX, bleeps, mastering")
    s.add_argument("--no-stems", action="store_true", help="skip Demucs; bleeps then mute the whole mix")

    s = add("render", cmd_render, "render the video (or a slice)")
    s.add_argument("--preview", action="store_true", help="960x540, faster")
    s.add_argument("--uncensored", action="store_true", help="no lyric bars, and the unbleeped mix if there is one")
    s.add_argument("--start", type=float, default=0.0)
    s.add_argument("--end", type=float)
    s.add_argument("--name")
    s.add_argument("--workers", type=int)

    add("demo", cmd_demo, "synthesize a test song into a project")
    add("status", cmd_status, "what's done for a project")
    add("list", cmd_list, "list projects")
    s = add("ui", cmd_ui, "open Cuesheet, the web UI")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")

    a = ap.parse_args(argv)
    from .keys import load_env
    load_env()
    a.fn(a)


if __name__ == "__main__":
    main()
