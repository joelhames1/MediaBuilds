"""Cuesheet: the local web UI for songvid. Start with `songvid ui`."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .. import keys
from ..llm import LLMUnavailable
from ..project import PROJECTS_DIR, Project, load_config
from ..schemas import Analysis, SongSpec, Storyboard, SunoResult, Timing
from . import chat, state
from .jobs import JobBusy, manager

STATIC = Path(__file__).resolve().parent / "static"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")

@asynccontextmanager
async def lifespan(app):
    manager.bind(asyncio.get_running_loop())
    yield


app = FastAPI(title="Cuesheet", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

@app.exception_handler(ValidationError)
async def bad_input(request: Request, exc: ValidationError):
    from fastapi.responses import JSONResponse
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(x) for x in first.get("loc", ()))
    return JSONResponse({"detail": f"Invalid {where or 'input'}: {first.get('msg', 'bad value')}"}, status_code=422)


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
           "font-src https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
           "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
MAX_AUDIO = 300 * 1024 * 1024
MAX_TEXT = 5 * 1024 * 1024


def _hostname(netloc: str) -> str:
    netloc = netloc.strip().lower()
    if netloc.startswith("["):
        return netloc.split("]")[0] + "]"
    return netloc.split(":")[0]


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Cuesheet has no login, so it only answers to this machine.

    The Host check stops DNS rebinding (a web page whose domain points at 127.0.0.1); the Origin
    check stops other sites' pages from posting to the API through your browser (CSRF).
    """
    from fastapi.responses import PlainTextResponse
    if _hostname(request.headers.get("host", "")) not in LOCAL_HOSTS:
        return PlainTextResponse("Cuesheet only answers on localhost.", status_code=403)
    if request.method not in SAFE_METHODS:
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        if (origin and _hostname(origin.split("://", 1)[-1]) not in LOCAL_HOSTS) or site == "cross-site":
            return PlainTextResponse("Requests from other websites are blocked.", status_code=403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/files/"):
        resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"  # project files never run as pages
    else:
        resp.headers.setdefault("Content-Security-Policy", APP_CSP)
    return resp


def _save_upload(src, dest: Path, limit: int, what: str) -> None:
    """Copy an upload to disk, refusing anything over `limit` bytes."""
    n = 0
    with dest.open("wb") as fh:
        while chunk := src.read(1024 * 1024):
            n += len(chunk)
            if n > limit:
                raise HTTPException(413, f"That {what} is over {limit // (1024 * 1024)} MB.")
            fh.write(chunk)


def proj(slug: str, must_exist: bool = True) -> Project:
    if not SLUG_RE.match(slug):
        raise HTTPException(400, "Project names use lowercase letters, numbers and dashes.")
    p = Project(slug)
    if must_exist and not p.dir.exists():
        raise HTTPException(404, f"No project called {slug}")
    return p


def need(cond, msg, code=409):
    if not cond:
        raise HTTPException(code, msg)


# ---------------- projects ----------------

@app.get("/api/projects")
def list_projects():
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    out = [state.summary(Project(d.name)) for d in sorted(PROJECTS_DIR.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
           if d.is_dir() and SLUG_RE.match(d.name)]
    return {"projects": out}


@app.post("/api/projects")
def create_project(body: dict = Body(...)):
    title = (body.get("title") or "").strip() or "Untitled"
    slug = body.get("slug") or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "untitled"
    p = proj(slug, must_exist=False)
    need(not p.dir.exists(), f"A project called {slug} already exists.")
    from ..stages.song import save
    p.ensure()
    save(p, SongSpec(title=title, style="", lyrics="[Verse 1]\n\n[Chorus]\n\n[End]"), body.get("brief") or None)
    state.log(p, "you", f"Created {title}")
    return {"slug": slug}


@app.post("/api/demo")
def make_demo():
    from ..demo import build
    slug = "demo"
    n = 2
    while Project(slug).dir.exists():
        slug, n = f"demo-{n}", n + 1
    p = Project(slug).ensure()
    build(p)
    state.log(p, "cuesheet", "Synthesized demo song")
    return {"slug": slug}


@app.get("/api/projects/{slug}")
def get_project(slug: str):
    p = proj(slug)
    data = state.full(p, manager.running_stages(slug))
    data["jobs"] = manager.for_project(slug)
    data["config"] = {"rate_per_second": load_config(p).get("ui", {}).get("video_rate_per_second", 0.10),
                      "video_model": load_config(p)["generate"]["video_model"],
                      "suno_mode": load_config(p)["suno"]["mode"],
                      "video_durations": load_config(p)["generate"]["video_durations"],
                      "max_stretch": load_config(p)["generate"].get("max_stretch", 1.3)}
    return data


@app.put("/api/projects/{slug}/song")
def put_song(slug: str, body: dict = Body(...)):
    p = proj(slug)
    spec = SongSpec.model_validate(body)
    from ..stages.song import save
    old = p.read(p.song, SongSpec) if p.song.exists() else None
    save(p, spec)
    what = [k for k in ("title", "style", "exclude", "lyrics", "settings") if not old or getattr(old, k) != getattr(spec, k)]
    if what:
        state.log(p, "you", "Edited " + ", ".join(what))
    return {"ok": True}


@app.put("/api/projects/{slug}/brief")
def put_brief(slug: str, body: dict = Body(...)):
    p = proj(slug)
    p.path("brief.md").write_text((body.get("brief") or "").strip() + "\n")
    return {"ok": True}


@app.post("/api/projects/{slug}/song/import")
def import_song(slug: str, body: dict = Body(...)):
    p = proj(slug)
    from ..stages.song import import_markdown
    try:
        import_markdown(p, body.get("markdown", ""))
    except ValueError as e:
        raise HTTPException(422, str(e))
    state.log(p, "you", "Imported the song from a chat draft")
    return {"ok": True}


@app.put("/api/projects/{slug}/storyboard")
def put_storyboard(slug: str, body: dict = Body(...)):
    p = proj(slug)
    board = Storyboard.model_validate(body.get("storyboard"))
    if p.analysis.exists():
        from ..stages.storyboard import tidy
        an = p.read(p.analysis, Analysis)
        grid = sorted(set(an.downbeats) | {s.start for s in an.sections})
        for s in board.shots[1:]:  # hand edits always land on a bar line
            s.start = min(grid, key=lambda g: abs(g - s.start)) if grid else s.start
        ids = [s.id for s in sorted(board.shots, key=lambda s: s.start)]
        board = tidy(board, an)
        if len(ids) == len(board.shots):
            for s, i in zip(board.shots, ids):
                s.id = i
    p.write(p.storyboard, board)
    if body.get("note"):
        state.log(p, "you", body["note"])
    return {"storyboard": board.model_dump(mode="json")}


@app.put("/api/projects/{slug}/timing")
def put_timing(slug: str, body: dict = Body(...)):
    p = proj(slug)
    tm = Timing.model_validate(body.get("timing"))
    for ln in tm.lines:
        ln.start = tm.words[ln.words[0]].start
        ln.end = tm.words[ln.words[-1]].end
    p.write(p.timing, tm)
    from ..stages.align import write_exports
    write_exports(p, tm)
    state.record(p, "timing")  # a manual fix is the new truth for these lyrics + audio
    if body.get("note"):
        state.log(p, "you", body["note"])
    return {"ok": True}


@app.post("/api/projects/{slug}/audio")
async def upload_audio(slug: str, file: UploadFile = File(...), aligned: UploadFile | None = File(None),
                       url: str = Form("")):
    p = proj(slug)
    suffix = Path(file.filename or "song.mp3").suffix.lower() or ".mp3"
    need(suffix in (".mp3", ".wav", ".m4a", ".flac"), "Use an MP3, WAV, M4A or FLAC file.", 422)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"upload{suffix}"
        _save_upload(file.file, src, MAX_AUDIO, "audio file")
        al = None
        if aligned and aligned.filename:
            al = Path(tmp) / Path(aligned.filename).name  # keep the extension: .json, .lrc or .srt
            _save_upload(aligned.file, al, MAX_TEXT, "timing file")
        from ..stages.suno import import_audio
        import_audio(p, src, al, url or None)
    res = p.read(p.suno, SunoResult)
    res.clips[0].id = Path(file.filename or "take").stem
    res.chosen = res.clips[0].id
    p.write(p.suno, res)
    state.log(p, "you", f"Imported {file.filename}")
    return {"ok": True}


@app.post("/api/projects/{slug}/timed-lyrics")
async def upload_timed_lyrics(slug: str, files: list[UploadFile] = File(...)):
    """Timed lyrics from elsewhere (.lrc / .srt / Suno aligned-words .json), then re-align with them."""
    p = proj(slug)
    from ..stages.align import save_timed_lyrics
    saved = []
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            src = Path(tmp) / Path(f.filename or "lyrics.lrc").name
            _save_upload(f.file, src, MAX_TEXT, "timing file")
            try:
                saved.append(save_timed_lyrics(p, src).name)
            except ValueError as e:
                raise HTTPException(422, f"{f.filename}: {e}")
    state.log(p, "you", f"Imported timed lyrics: {', '.join(f.filename for f in files)}")
    job = None
    if p.song.exists():
        try:
            p.audio()
            job = _run_job(p, "align", {"method": "suno"}).public()
        except (FileNotFoundError, JobBusy):
            pass
    return {"saved": saved, "job": job}


@app.post("/api/projects/{slug}/pick")
def pick_take(slug: str, body: dict = Body(...)):
    p = proj(slug)
    from ..stages.suno import choose
    res = p.read(p.suno, SunoResult)
    choose(p, res, body["clip_id"])
    state.log(p, "you", f"Switched to take {body['clip_id']}")
    return {"ok": True}


@app.post("/api/projects/{slug}/approve")
def approve(slug: str, body: dict = Body(...)):
    p = proj(slug)
    from ..render.generate import approve as ap
    ap(p, body["shots"], value=bool(body.get("approved", True)), note=body.get("note", ""))
    word = "Approved" if body.get("approved", True) else "Sent back"
    state.log(p, "you", f"{word} still {', '.join(body['shots'])}" + (f": {body['note']}" if body.get("note") else ""))
    return {"ok": True}


# ---------------- jobs ----------------

def _run_job(p: Project, kind: str, args: dict):
    """Validate and queue a pipeline step. Returns the Job."""
    from ..stages import align as align_st, analyze as analyze_st, song as song_st, storyboard as sb_st, suno as suno_st
    from ..render import compositor, generate

    if kind == "song":
        brief = (args.get("brief") or "").strip()
        if brief:
            p.path("brief.md").write_text(brief + "\n")
        brief = brief or (p.path("brief.md").read_text() if p.path("brief.md").exists() else "")
        need(brief or args.get("revise"), "Write a brief first.")
        label = "Revise the song" if args.get("revise") else "Write the song"
        return manager.submit(p.slug, kind, label, lambda p, cfg, j: (song_st.generate(p, brief, cfg, args.get("revise")), None)[1])

    if kind == "suno_api":
        need(p.song.exists(), "Write the song first.")
        return manager.submit(p.slug, kind, "Generate in Suno (API)", lambda p, cfg, j: (suno_st.run_api(p, cfg), None)[1])

    if kind == "align":
        need(p.song.exists(), "Write the song first.")
        p.audio()

        def run(p, cfg, j):
            tm = align_st.run(p, cfg, args.get("method"))
            state.record(p, "timing")
            analyze_st.run(p, cfg)
            state.record(p, "analysis")
            if p.storyboard.exists():
                _refit(p)
            return {"source": tm.source}
        return manager.submit(p.slug, kind, "Align lyrics + analyze", run)

    if kind == "analyze":
        def run(p, cfg, j):
            analyze_st.run(p, cfg)
            state.record(p, "analysis")
        return manager.submit(p.slug, kind, "Analyze audio", run)

    if kind == "refit":
        need(p.storyboard.exists(), "There is no storyboard yet.")
        return manager.submit(p.slug, kind, "Re-fit shots to bar lines", lambda p, cfg, j: _refit(p))

    if kind == "storyboard":
        need(p.analysis.exists(), "Align and analyze first.")
        mode, direction = args.get("mode", "hybrid"), args.get("direction", "")
        from ..schemas import ArtStyle
        art = ArtStyle.model_validate(args["art"]) if args.get("art") else None

        def run(p, cfg, j):
            if args.get("heuristic"):
                an = p.read(p.analysis, Analysis)
                board = sb_st.tidy(sb_st.heuristic(an, mode), an)
                board.art = art or (p.read(p.storyboard, Storyboard).art if p.storyboard.exists() else None)
                p.write(p.storyboard, board)
            else:
                from ..progress import report
                report(None, "Claude is directing")
                sb_st.generate(p, cfg, mode, direction, art)
            if mode == "claude":  # a new shot list means new drawings (paid AI clips are left alone)
                import shutil as _sh
                _sh.rmtree(p.path("art"), ignore_errors=True)
                for sh in p.read(p.storyboard, Storyboard).shots:
                    (p.stills_dir / f"{sh.id}_key.png").unlink(missing_ok=True)
                    (p.clips_dir / f"{sh.id}.mp4").unlink(missing_ok=True)
            state.record(p, "board")
            p.path("approvals.json").unlink(missing_ok=True)
        return manager.submit(p.slug, kind, "Storyboard" + (" (heuristic)" if args.get("heuristic") else " (Claude)"), run,
                              then=lambda: _run_job(p, "stills", {}))

    if kind == "stills":
        need(p.storyboard.exists(), "There is no storyboard yet.")

        def run(p, cfg, j):
            compositor.stills(p)
            state.record(p, "look")
        return manager.submit(p.slug, kind, "Render stills", run)

    if kind == "keyframes":
        need(p.storyboard.exists(), "There is no storyboard yet.")
        shots = args.get("shots") or None

        def run(p, cfg, j):
            from ..progress import report
            report(None, "asking fal.ai for stills")
            generate.keyframes(p, shots, redo=bool(args.get("redo")))
        return manager.submit(p.slug, kind, "AI keyframes" + (f" ({', '.join(shots)})" if shots else ""), run,
                              then=lambda: _run_job(p, "stills", {}))

    if kind == "draw":
        need(p.storyboard.exists(), "There is no storyboard yet.")
        board = p.read(p.storyboard, Storyboard)
        need(any(s.source == "claude" for s in board.shots), "No shots are set to Claude-drawn.")
        shots = args.get("shots") or None
        from ..art.director import draw_scenes

        def run(p, cfg, j):
            if args.get("restyle"):  # new style: new kit, and every scene redrawn with it
                from ..art.render import kit_path
                kit_path(p).unlink(missing_ok=True)
            notes = state.approvals(p)
            if shots and len(shots) == 1 and (args.get("note") or args.get("redo")):
                note = args.get("note") or notes.get(shots[0], {}).get("note", "") or "Try a fresh take on this shot."
                return {"scenes": draw_scenes(p, shots, note=note, redo=True)}
            return {"scenes": draw_scenes(p, shots, redo=bool(args.get("redo")))}
        label = "Claude draws " + (f"{', '.join(shots)}" if shots else "the scenes")
        return manager.submit(p.slug, kind, label, run, then=lambda: _run_job(p, "stills", {}))

    if kind == "draw_render":
        need(p.storyboard.exists(), "There is no storyboard yet.")
        board = p.read(p.storyboard, Storyboard)
        ap = state.approvals(p)
        from ..art.render import render_clips, scene_path
        ids = args.get("shots") or [s.id for s in board.shots if s.source == "claude" and ap.get(s.id, {}).get("approved")
                                    and scene_path(p, s.id).exists()]
        need(ids, "No approved Claude-drawn scenes to render.")
        return manager.submit(p.slug, kind, f"Render {len(ids)} Claude scene(s)",
                              lambda p, cfg, j: {"clips": [c.name for c in render_clips(p, ids)]})

    if kind == "animate":
        need(args.get("confirm") is True, "Animating costs money; confirm it in the Picture step.")
        plan = generate.plan_animate(p, args.get("shots") or None)
        need(plan, "Nothing is ready to animate.")

        def run(p, cfg, j):
            from ..progress import report
            report(0.0, f"{len(plan)} clips queued at fal.ai")
            generate.animate(p, [s.id for s, _ in plan])
        state.log(p, "you", f"Approved generating {len(plan)} clip(s), {sum(d for _, d in plan)} s of video")
        return manager.submit(p.slug, kind, f"Animate {len(plan)} shot(s)", run)

    if kind == "render":
        need(p.storyboard.exists() and p.analysis.exists(), "Storyboard and analysis are needed first.")
        preview = bool(args.get("preview", True))
        start, end = float(args.get("start") or 0), args.get("end")
        an = p.read(p.analysis, Analysis)
        end = float(end) if end else an.duration
        full_song = start <= 0.01 and end >= an.duration - 0.01
        name = ("final" if not preview and full_song else None)
        label = f"{'Preview' if preview else 'Final'} render " + ("full song" if full_song else f"{start:.0f}-{end:.0f} s")
        return manager.submit(p.slug, kind, label, lambda p, cfg, j: {"file": str(compositor.render(
            p, preview=preview, start=start, end=end, name=name).relative_to(p.dir))})

    if kind == "rebuild":
        st = state.statuses(p, manager.running_stages(p.slug))
        chain = []
        if st["timing"]["state"] == "stale":
            chain.append(("align", {}))
        elif st["board"]["state"] == "stale":
            chain.append(("refit", {}) if p.analysis.exists() and state._fresh(p, "analysis", p.analysis) else ("align", {}))
        if p.storyboard.exists():
            chain.append(("stills", {}))
        need(chain, "Nothing is out of date.")
        first = None
        for k, a in chain:
            try:
                jb = _run_job(p, k, a)  # cpu lane is single-threaded, so these run in order
            except JobBusy:
                continue
            first = first or jb
        need(first, "That rebuild is already running.")
        return first

    raise HTTPException(400, f"Unknown step {kind}")


def _refit(p: Project) -> None:
    from ..stages.storyboard import tidy
    an = p.read(p.analysis, Analysis)
    board = p.read(p.storyboard, Storyboard)
    ids = [s.id for s in board.shots]
    board = tidy(board, an)
    if len(ids) == len(board.shots):
        for s, i in zip(board.shots, ids):
            s.id = i
    p.write(p.storyboard, board)
    state.record(p, "board")


@app.post("/api/projects/{slug}/jobs")
def start_job(slug: str, body: dict = Body(...)):
    p = proj(slug)
    try:
        job = _run_job(p, body.get("kind", ""), body.get("args") or {})
    except (FileNotFoundError, JobBusy) as e:
        raise HTTPException(409, str(e))
    return {"job": job.public()}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    try:
        return {"job": manager.cancel(job_id).public()}
    except KeyError:
        raise HTTPException(404, "No such job (the server may have restarted).")
    except JobBusy as e:
        raise HTTPException(409, str(e))


@app.post("/api/projects/{slug}/jobs/cancel-queued")
def cancel_queued(slug: str):
    proj(slug)
    done = []
    for j in list(manager.jobs.values()):
        if j.slug == slug and j.status == "queued":
            try:
                done.append(manager.cancel(j.id).id)
            except JobBusy:
                pass  # it started in the meantime
    return {"cancelled": done}


@app.get("/api/events")
async def events(request: Request):
    q = manager.subscribe()

    async def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            manager.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---------------- Claude panel ----------------

@app.post("/api/projects/{slug}/chat")
async def post_chat(slug: str, body: dict = Body(...)):
    p = proj(slug)
    msg = (body.get("message") or "").strip()
    need(msg, "Say something first.", 422)

    def queue_step(step, inp):
        kind = {"preview": "render"}.get(step, step)
        if kind in ("draw", "keyframes"):  # these spend money: the chat may start a few, the buttons do batches
            board = p.read(p.storyboard, Storyboard) if p.storyboard.exists() else None
            src = "claude" if kind == "draw" else "generated"
            todo = [s for s in (board.shots if board else []) if s.source == src and not (p.stills_dir / f"{s.id}_key.png").exists()]
            if len(todo) > 3:
                return f"{len(todo)} shots would be paid for; ask the artist to press the button in Look instead."
        args = {"preview": True, "start": inp.get("start", 0), "end": inp.get("end")} if step == "preview" else {}
        try:
            return _run_job(p, kind, args).id
        except JobBusy:
            return -1

    try:
        out = await asyncio.to_thread(chat.turn, p, msg, manager.running_stages(slug), queue_step)
    except LLMUnavailable as e:
        raise HTTPException(503, str(e))
    manager.publish({"type": "project", "slug": slug})
    return out


@app.post("/api/projects/{slug}/chat/undo")
def chat_undo(slug: str, body: dict = Body(...)):
    p = proj(slug)
    need(chat.undo(p, body.get("turn", "")), "That change can no longer be undone.")
    return {"ok": True}


# ---------------- keys ----------------

@app.get("/api/keys")
def get_keys():
    cfg = load_config()
    return {"keys": keys.status(), "env_file": str(keys.ENV_FILE),
            "models": {"claude": cfg["llm"]["model"], "image": cfg["generate"]["image_model"],
                       "video": cfg["generate"]["video_model"], "whisper": cfg["align"]["whisper_model"]}}


@app.put("/api/keys/{name}")
def put_key(name: str, body: dict = Body(...)):
    need(name in keys.KEYS, "Unknown key.", 404)
    st = next(k for k in keys.status() if k["name"] == name)
    need(st["source"] != "shell", f"{name} is set in your shell environment, which wins over this file. Change it there.")
    try:
        keys.save_key(name, body.get("value", ""))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"keys": keys.status()}


@app.post("/api/keys/{name}/test")
def test_key(name: str):
    need(name in keys.KEYS, "Unknown key.", 404)
    ok, msg = keys.test(name)
    return {"ok": ok, "message": msg}


# ---------------- files + static ----------------

@app.get("/files/{slug}/{path:path}")
def get_file(slug: str, path: str):
    p = proj(slug)
    f = (p.dir / path).resolve()
    need(f.is_relative_to(p.dir.resolve()) and f.is_file(), "Not found", 404)
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    if host not in LOCAL_HOSTS:
        raise SystemExit("Cuesheet has no login yet, so it only runs on localhost. Put it behind real auth before sharing it.")
    import threading
    import webbrowser

    import uvicorn

    import socket

    keys.load_env()
    for candidate in range(port, port + 20):  # step past ports something else is using
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            if sk.connect_ex((host, candidate)) != 0:
                break
    else:
        raise SystemExit(f"Ports {port}-{port + 19} are all in use. Try: songvid ui --port 9100")
    if candidate != port:
        print(f"Port {port} is in use (maybe another Cuesheet window is still running); using {candidate}.")
    port = candidate
    url = f"http://{host}:{port}/"
    print(f"Cuesheet running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
