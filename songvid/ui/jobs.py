"""Background jobs for the UI: one CPU lane (renders, alignment) and one network lane
(Claude, Suno API, fal.ai). Progress events are pushed to SSE subscribers."""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Callable

from ..llm import describe, drain
from ..progress import set_reporter
from ..project import Project, load_config
from . import state

STAGE_OF = {"song": "song", "suno_api": "suno", "align": "timing", "analyze": "board", "storyboard": "board",
            "refit": "board", "stills": "look", "keyframes": "look", "draw": "look", "animate": "picture",
            "draw_render": "picture", "render": "render"}
CPU = {"align", "analyze", "refit", "stills", "render", "draw_render"}


class JobBusy(RuntimeError):
    """An identical job is already queued or running for this project."""


@dataclass
class Job:
    id: int
    slug: str
    kind: str
    label: str
    status: str = "queued"  # queued | running | done | error | cancelled
    progress: float | None = None
    message: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    result: dict = field(default_factory=dict)
    live: str | None = None  # path of a live preview image, relative to the project

    def public(self) -> dict:
        return asdict(self)


class JobManager:
    def __init__(self):
        self.jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._lanes = {"cpu": ThreadPoolExecutor(1, thread_name_prefix="cpu"),
                       "net": ThreadPoolExecutor(3, thread_name_prefix="net")}
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ---- events ----
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict) -> None:
        if not self._loop:
            return

        def push():
            for q in list(self._subs):
                if not q.full():
                    q.put_nowait(event)
        self._loop.call_soon_threadsafe(push)

    # ---- jobs ----
    def running_stages(self, slug: str) -> set[str]:
        return {STAGE_OF.get(j.kind, j.kind) for j in self.jobs.values()
                if j.slug == slug and j.status in ("queued", "running")}

    def for_project(self, slug: str, limit: int = 20) -> list[dict]:
        js = sorted((j for j in self.jobs.values() if j.slug == slug), key=lambda j: j.id, reverse=True)
        return [j.public() for j in js[:limit]]

    def submit(self, slug: str, kind: str, label: str, fn: Callable[[Project, dict, Job], dict | None],
               then: Callable[[], None] | None = None) -> Job:
        for j in self.jobs.values():  # double clicks must not double-spend
            if j.slug == slug and j.kind == kind and j.label == label and j.status in ("queued", "running"):
                raise JobBusy(f"{label} is already {j.status}.")
        job = Job(next(self._ids), slug, kind, label)
        self.jobs[job.id] = job
        self.publish({"type": "job", "job": job.public()})
        lane = "cpu" if kind in CPU else "net"
        self._lanes[lane].submit(self._run, job, fn, then)
        return job

    def cancel(self, job_id: int) -> Job:
        """Cancel a job that hasn't started. Running jobs can't be stopped safely mid-step."""
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.status != "queued":
                raise JobBusy(f"{job.label} is {job.status}; only queued jobs can be cancelled.")
            job.status, job.finished, job.message = "cancelled", time.time(), "Cancelled before it started"
        state.log(Project(job.slug), "you", f"Cancelled {job.label}")
        self.publish({"type": "job", "job": job.public()})
        self.publish({"type": "project", "slug": job.slug})
        return job

    def _run(self, job: Job, fn, then) -> None:
        p = Project(job.slug)
        with self._lock:  # a cancel may have landed while this waited in the queue
            if job.status == "cancelled":
                return
            job.status, job.started = "running", time.time()
        self.publish({"type": "job", "job": job.public()})
        last = [0.0]

        def reporter(frac, msg):
            job.progress, job.message = frac, msg
            if job.kind == "render" and (p.renders_dir / ".live.jpg").exists():
                job.live = "renders/.live.jpg"
            now = time.time()
            if now - last[0] > 0.25 or (frac is not None and frac >= 1):
                last[0] = now
                self.publish({"type": "job", "job": job.public()})

        set_reporter(reporter)
        drain()  # this lane thread is reused; start the job with a clean slate
        try:
            job.result = fn(p, load_config(p), job) or {}
            calls = drain()
            if calls:  # Claude ran in this job: say which model actually answered and what it cost
                job.result["ai"] = calls
                job.message = describe(calls)
            if then:  # queue follow-ups before this job reads as finished, so there is no idle gap
                try:
                    then()
                except JobBusy:
                    pass  # the follow-up is already queued
            job.status, job.progress = "done", 1.0
            state.log(p, "cuesheet", f"{job.label}: done" + (f" · {job.message}" if job.result.get("ai") else ""))
        except Exception as e:  # surface the reason in the UI
            job.status, job.error = "error", f"{e.__class__.__name__}: {e}"
            job.message = job.error
            state.log(p, "cuesheet", f"{job.label}: failed ({e})")
            traceback.print_exc()
        finally:
            set_reporter(None)
            job.finished = time.time()
            self.publish({"type": "job", "job": job.public()})
            self.publish({"type": "project", "slug": job.slug})


manager = JobManager()
