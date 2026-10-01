# songvid

Lyrics → Suno → word-timed audio → AI-directed music video. A working prototype.

```
 brief ──► song.json ──► Suno ──► audio.mp3 ──► timing.json ──► analysis.json ──► storyboard.json ──► stills ──► render.mp4
          (Claude +     (manual   (+ Suno's     (word-level     (beats, bars,     (Claude as         (approve   (procedural,
           your Suno     or API)   timing if     lyric timing)   sections,         director)          before     AI stills,
           skill)                  API)                          energy curves)                       spending)  or AI clips)
```

Every arrow is a CLI command, and every box is a plain JSON or media file in `projects/<slug>/`. You can
hand-edit any of them, or have Claude Code edit them in chat, and pick up from the next stage.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'           # core: numpy, librosa, anthropic, pillow
pip install -e '.[align]'         # optional: Whisper forced alignment (pulls in torch)
brew install ffmpeg               # or apt install ffmpeg
cp songvid.example.yaml songvid.yaml   # optional overrides
```

Keys (all optional, each one turns on more automation):

| Env var | Turns on | Without it |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Claude writes the song and directs the video | import a song from a chat; heuristic storyboard |
| `SUNOAPI_KEY` | fully automatic Suno generation + Suno's own word timing | you generate on suno.com and import the MP3 |
| `FAL_KEY` | AI keyframe stills and image-to-video clips | procedural visuals only |

## Try it with no keys at all

```bash
songvid demo demo                      # synthesizes a 65 s test song with known word timing
songvid prep demo --heuristic          # align + analyze + storyboard + contact sheet
songvid render demo --preview          # 960x540 in about a minute
open projects/demo/renders/preview.mp4
```

## Cuesheet, the app

```bash
pip install -e '.[ui]'
songvid ui                 # opens http://127.0.0.1:8765
```

Everything the CLI does, with a timeline. The strip across the top shows each step (Song, Suno, Timing,
Board, Look, Picture, Render) as done, running, needs you (amber), or out of date (hatched). Edit the
lyrics and everything downstream goes out of date until you press Rebuild. Long jobs (alignment,
stills, renders, AI generation) run in the background with live progress; renders show their latest frame.
The Claude panel edits the storyboard and song for you, and every change can be undone.

API keys go in **API keys** (top right). Each card says what the key unlocks, links to where you get
it, saves it to `.env` in this folder (owner-only, git-ignored), and has a Test button that checks it
without spending credits. The CLI reads the same `.env`.

## The real walkthrough

### 1. Write the song

```bash
songvid song porch-light --brief "A quiet folk song for my mom about the porch light she leaves on. \
Details: she still uses the 1998 kettle, waves from the driveway until the car turns the corner..."
```

This uses your local `suno-v6-songwriting` skill as the system prompt when it can find it
(`~/.claude/skills/**/suno-v6-songwriting/SKILL.md`, configurable). It writes `song.json` and
`suno_paste.md` (the paste-ready Style / Exclude / Lyrics blocks plus settings).

Revise one variable at a time: `songvid song porch-light --revise "chorus is too wordy, more open vowels"`.

Prefer chatting? Write the song with Claude and your skill as usual, save the reply as `draft.md`, then
`songvid song porch-light --import draft.md`. It pulls the code blocks out by their headings.

### 2. Make it in Suno

**Manual (recommended to start).** `songvid suno porch-light` prints the blocks and steps. Generate
on suno.com, audition in the browser, download only the keeper (downloads are your scarce resource on Pro), then:

```bash
songvid suno porch-light --import ~/Downloads/porch-light.mp3 --url https://suno.com/song/...
```

**Automatic.** `songvid suno porch-light --mode api` submits to a sunoapi.org-compatible service, polls,
downloads both takes, and fetches Suno's own aligned words. Switch takes with `--pick 2`.
Caveat: Suno has no official API. These services generate on *their* accounts, so read their
terms, and don't assume your Pro plan's commercial rights cover those songs. Field names in
`songvid/stages/suno.py` follow sunoapi.org's docs; other providers may differ slightly.

Pipeline rule: we never pull your suno.com session token. Word timing comes from the API
provider, or from forced alignment.

### 3. Prep: timing, analysis, storyboard, stills

```bash
songvid prep porch-light --mode internal --direction "memory and light; warm amber vs cold blue; no people"
```

- **align**: word-level timing into `timing.json`, plus `lyrics.srt` and `lyrics.lrc`. Load the SRT
  in VLC next to the MP3 to sanity-check it. Methods, tried in order (`--method` forces one):
  `suno` (aligned words from the API or a dropped-in `suno_aligned.json`), `whisper` (stable-ts
  forced alignment of your known lyrics, the good one), `even` (energy-based guess, no deps).
  Setting `align.isolate_vocals: true` runs Demucs first, which helps on dense mixes.
- **analyze**: tempo, beats, bar lines, sections (from the lyric tags plus intro, outro and
  instrumental gaps), and per-frame curves (rms, low, high, onset, beat, energy) in `features.npz`.
- **storyboard**: Claude gets the lyrics with exact line timings, the sections and energy, and the
  bar lines, and returns a shot list. Cuts get snapped to bar lines afterwards. `--mode`:
  - `internal`: all procedural (option a). Free, local, deterministic.
  - `external`: all AI-generated imagery (option b).
  - `hybrid`: AI imagery for the moments that need it, procedural everywhere else. Usually the best
    quality per dollar.
- **stills**: one frame per shot and `stills/contact_sheet.jpg`. Approve the look here before any long render.

`storyboard.json` is meant to be edited: change scenes, palettes, which shots show lyrics, transitions.
Then re-run `songvid stills`.

### 4a. Internal (procedural) video

```bash
songvid render porch-light --preview --start 30 --end 60    # 30 s vertical slice first
songvid render porch-light                                  # full 1080p
```

Scenes: `nebula`, `smoke` (ink in water), `rays` (volumetric light), `particles` (fly-through),
`waves` (ridgelines), `embers`. All are audio-reactive (bass drives travel, beats punch the zoom,
loudness lifts exposure). Transitions are `cut` / `fade` / `flash`, then grain, vignette, a 2.39:1
letterbox, and word-timed serif italic lyrics, where each word brightens as it's sung.

### 4b. External (AI-generated) video

```bash
songvid storyboard porch-light --mode hybrid
songvid keyframes porch-light            # text-to-image per generated shot (cheap)
songvid stills porch-light               # contact sheet now shows the AI stills
songvid approve porch-light s03 s05 s09  # or: approve all / --reject --note "less saturated" + keyframes --redo
songvid render porch-light --preview     # animatic: approved stills with slow push-ins, free
songvid animate porch-light --yes        # image-to-video on approved shots only (costs money)
songvid render porch-light               # clips are fitted to shot length, lyrics composited on top
```

Any shot without a clip falls back to its still, and without a still, to its procedural scene. So
you can render at every step, and spend money only on the shots that earn it. Models are set in
config (`generate.image_model`, `generate.video_model`). The default is Kling 3 via fal.ai;
Veo 3.1, Seedance and friends are a config change away, and you adjust `video_args` if a model
wants different fields.

## Files in a project

```
projects/<slug>/
  brief.md  song.json  suno_paste.md       # stage 1
  suno.json  audio.mp3  suno_aligned.json  # stage 2
  timing.json  lyrics.srt  lyrics.lrc      # align
  analysis.json  features.npz              # analyze
  storyboard.json  approvals.json          # direct
  stills/  clips/  renders/                # pictures
```

## Known limits

- The `even` aligner is a placeholder. It nails the synthetic demo, but on real mixes expect line
  starts to be off by seconds. Install `.[align]` for real work.
- The Claude, sunoapi and fal calls are written against current docs but weren't exercised live
  while this was built (no keys in the build sandbox). The tests mock Claude. Expect a field
  name or two to need adjusting on first contact.
- Procedural scenes render on the CPU at about 3 fps per worker at 1080p (measured on 3 workers:
  17 s of video in 43 s). A 3.5-minute song takes about 10 minutes on a 4-core machine; `--preview`
  is roughly 3x faster.
- There's also a Blender path (your `blender-music-video` skill) for photoreal 3D, driven by the
  same `timing.json` and `features.npz`. Not wired in yet.
