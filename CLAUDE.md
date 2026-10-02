# songvid: notes for Claude Code

Pipeline: `song.json` → Suno → `audio.*` → `timing.json` → `analysis.json` → `storyboard.json` → stills → render.
Each stage is a `songvid <cmd> <slug>` command, and every artifact is a file in `projects/<slug>/`. Schemas
live in `songvid/schemas.py`. Read README.md for the user-facing walkthrough.

When Joel asks for a song or video in chat:
- Songwriting: use the `suno-v6-songwriting` skill, then write `projects/<slug>/song.json` directly
  (fields: title, style, style_terse, exclude, lyrics, settings{model, variety, style_influence, weirdness,
  max_mode, vocal_gender}, notes, references), then run `songvid song <slug> --refresh` to validate it
  and rebuild `suno_paste.md`.
- Never pull Joel's suno.com session token. Timing comes from the API provider, a JSON he supplies, or forced alignment.
- Directing: you may edit `storyboard.json` by hand instead of calling the API. Keep shots contiguous
  from 0 to duration and on bar lines (`analysis.json` → downbeats). Then run `songvid stills <slug>` and
  look at `stills/contact_sheet.jpg` before rendering anything long.
- His taste: cinematic and abstract over cartoonish; lyrics on screen for choruses and emotional
  lines only; serif italic; short polished segments; stills approved before long renders; a 30 s
  slice (`--preview --start --end`) before a full render.
- `songvid animate` spends money. Never pass `--yes` without his explicit go-ahead for that run.

Dev: `. .venv/bin/activate && python -m pytest -q tests` (about 15 s, no network). `songvid demo <slug>`
makes a synthetic song for end-to-end checks.

UI (Cuesheet): `songvid ui` serves `songvid/ui/` (FastAPI) + `songvid/ui/static/` (plain JS, no build). Stage
status and staleness live in `songvid/ui/state.py`; background jobs in `ui/jobs.py`; the Claude panel's tools
in `ui/chat.py`. Keys load from the repo-root `.env` via `songvid/keys.py`; never print key values.

Claude-drawn shots (`source: "claude"`): `songvid/art/` has the runtime page (`runtime.html`, the scene contract and
`U` helpers), the renderer (headless Chromium via Playwright, network blocked) and the art director prompts
(`prompts/art_director.md`). Scenes live in `projects/<slug>/art/`; you may write or edit them by hand, then run
the `draw_render` job or `render_clips()`.

Security (see SECURITY.md): the UI answers only on localhost (Host + Origin checks in `ui/server.py`), so tests use
`TestClient(app, base_url="http://127.0.0.1")`. Shot ids become file names and must match `^[A-Za-z0-9_-]{1,40}$`.
