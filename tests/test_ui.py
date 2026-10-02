import json
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from songvid import keys, project as proj  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(proj, "PROJECTS_DIR", tmp_path / "projects")
    from songvid.ui import server
    monkeypatch.setattr(server, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(keys, "ENV_FILE", tmp_path / ".env")
    with TestClient(server.app) as c:
        yield c


def wait(c, slug, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = c.get(f"/api/projects/{slug}").json()
        if not any(j["status"] in ("queued", "running") for j in d["jobs"]):
            return d
        time.sleep(0.3)
    raise TimeoutError


def test_demo_flow_and_staleness(client):
    slug = client.post("/api/demo").json()["slug"]
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "align"})
    d = wait(client, slug)
    assert d["status"]["timing"]["state"] == "done"
    assert d["analysis"]["downbeats"][:3] == pytest.approx([0.0, 2.5, 5.0], abs=0.05)
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "storyboard", "args": {"heuristic": True, "mode": "internal"}})
    time.sleep(0.5)
    d = wait(client, slug)
    assert d["status"]["look"]["state"] == "done"  # stills were queued automatically
    assert all("thumb" in f for f in d["shot_files"].values())
    assert d["status"]["render"]["state"] == "needs"  # everything upstream is done: render is next

    song = d["song"]
    song["style"] += " More reverb."
    client.put(f"/api/projects/{slug}/song", json=song)
    assert client.get(f"/api/projects/{slug}").json()["status"]["timing"]["state"] == "done"
    song["lyrics"] = song["lyrics"].replace("humming", "flickers")
    client.put(f"/api/projects/{slug}/song", json=song)
    assert client.get(f"/api/projects/{slug}").json()["status"]["timing"]["state"] == "stale"

    client.post(f"/api/projects/{slug}/jobs", json={"kind": "rebuild"})
    time.sleep(0.5)
    d = wait(client, slug)
    assert d["status"]["timing"]["state"] == "done" and d["status"]["look"]["state"] == "done"
    assert any(w["text"] == "flickers" for w in d["timing"]["words"])


def test_storyboard_put_is_tidied_and_marks_look_stale(client):
    slug = client.post("/api/demo").json()["slug"]
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "align"})
    wait(client, slug)
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "storyboard", "args": {"heuristic": True}})
    time.sleep(0.5)
    d = wait(client, slug)
    board = d["storyboard"]
    board["shots"][1]["start"] = 11.1  # off the grid
    r = client.put(f"/api/projects/{slug}/storyboard", json={"storyboard": board, "note": "moved a cut"})
    shots = r.json()["storyboard"]["shots"]
    assert shots[1]["start"] == pytest.approx(10.0, abs=0.05) and shots[0]["end"] == shots[1]["start"]
    assert client.get(f"/api/projects/{slug}").json()["status"]["look"]["state"] == "stale"


def test_animate_requires_confirmation(client):
    slug = client.post("/api/demo").json()["slug"]
    r = client.post(f"/api/projects/{slug}/jobs", json={"kind": "animate", "args": {}})
    assert r.status_code == 409 and "costs money" in r.json()["detail"]


def test_keys_save_status_and_shell_wins(client, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    r = client.put("/api/keys/FAL_KEY", json={"value": "abc123-secret-value-xyz"})
    k = next(x for x in r.json()["keys"] if x["name"] == "FAL_KEY")
    assert k["set"] and k["source"] == ".env" and "secret" not in k["hint"]
    assert "FAL_KEY=abc123-secret-value-xyz" in keys.ENV_FILE.read_text()
    assert oct(keys.ENV_FILE.stat().st_mode)[-3:] == "600"
    client.put("/api/keys/FAL_KEY", json={"value": ""})
    assert "FAL_KEY" not in keys.ENV_FILE.read_text()
    monkeypatch.setattr(keys, "_shell", {"FAL_KEY"})
    monkeypatch.setenv("FAL_KEY", "from-shell")
    assert client.put("/api/keys/FAL_KEY", json={"value": "x"}).status_code == 409


def test_claude_tools_edit_storyboard(client):
    from songvid.project import Project
    from songvid.ui import chat

    slug = client.post("/api/demo").json()["slug"]
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "align"})
    wait(client, slug)
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "storyboard", "args": {"heuristic": True}})
    time.sleep(0.5)
    wait(client, slug)
    p, changes, jobs = Project(slug), [], []
    q = lambda step, inp: 1  # noqa: E731
    view = chat._tool(p, "get_project", {}, set(), q, changes, jobs)
    assert view["bar_lines"][:2] == pytest.approx([0.0, 2.5], abs=0.05) and view["lines"]
    first = view["storyboard"]["shots"][0]
    chat._tool(p, "update_shots", {"changes": [{"id": first["id"], "fields": {"scene": "rays", "intensity": 0.9}}]}, set(), q, changes, jobs)
    out = chat._tool(p, "split_shot", {"id": first["id"], "at": 5.3}, set(), q, changes, jobs)
    ids = [s["id"] for s in out["shots"]]
    assert ids[:2] == [first["id"], first["id"] + "b"]
    assert out["shots"][1]["start"] == pytest.approx(5.0, abs=0.05)
    with pytest.raises(ValueError):
        chat._tool(p, "update_shots", {"changes": [{"id": first["id"], "fields": {"start": 3}}]}, set(), q, changes, jobs)
    board = json.loads(p.storyboard.read_text())
    assert board["shots"][0]["scene"] == "rays" and board["shots"][1]["scene"] == "rays"


def test_duplicate_jobs_are_refused(client, monkeypatch):
    from songvid.stages import song as song_st

    gate = __import__("threading").Event()
    monkeypatch.setattr(song_st, "generate", lambda *a, **k: gate.wait(5))
    slug = client.post("/api/projects", json={"title": "Five and One", "brief": "first marathon"}).json()["slug"]
    assert client.post(f"/api/projects/{slug}/jobs", json={"kind": "song"}).status_code == 200
    r = client.post(f"/api/projects/{slug}/jobs", json={"kind": "song"})
    assert r.status_code == 409 and "already" in r.json()["detail"]
    gate.set()
    wait(client, slug)
    assert client.post(f"/api/projects/{slug}/jobs", json={"kind": "song"}).status_code == 200
    gate.set()
    wait(client, slug)


def test_timed_lyrics_upload_realigns(client):
    slug = client.post("/api/demo").json()["slug"]
    srt = "1\n00:00:10,000 --> 00:00:14,000\nPorch light humming in the rain\n\n2\n00:00:15,000 --> 00:00:19,000\nYour coat still hanging by the door\n"
    r = client.post(f"/api/projects/{slug}/timed-lyrics", files=[("files", ("song.srt", srt.encode(), "application/x-subrip"))])
    assert r.status_code == 200 and r.json()["saved"] == ["suno_lyrics.srt"] and r.json()["job"]
    d = wait(client, slug)
    assert d["timing"]["source"] == "Suno aligned words"  # the demo also ships word-level JSON, which wins
    bad = client.post(f"/api/projects/{slug}/timed-lyrics", files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert bad.status_code == 422


def test_animate_runs_in_parallel_with_progress(client, monkeypatch):
    import threading

    from songvid.progress import set_reporter
    from songvid.project import Project
    from songvid.render import generate

    assert generate.clip_seconds(6.4, [5, 10]) == 5 and generate.clip_seconds(6.6, [5, 10]) == 10
    slug = client.post("/api/demo").json()["slug"]
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "align"})
    wait(client, slug)
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "storyboard", "args": {"heuristic": True, "mode": "external"}})
    time.sleep(0.5)
    d = wait(client, slug)
    p = Project(slug)
    ids = [s["id"] for s in d["storyboard"]["shots"]][:4]
    for i in ids:
        (p.stills_dir / f"{i}_key.png").write_bytes(b"png")
    client.post(f"/api/projects/{slug}/approve", json={"shots": ids, "approved": True})

    live, peak, lock = [0], [0], threading.Lock()

    class FakeFal:
        def __init__(self, cfg):
            pass

        def run(self, model, args):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.3)
            with lock:
                live[0] -= 1
            return {"video": {"url": "x"}}

        def download(self, url, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"mp4")
            return dest

    monkeypatch.setattr(generate, "Fal", FakeFal)
    msgs = []
    set_reporter(lambda f, m: msgs.append((f, m)))
    try:
        generate.animate(p, ids)
    finally:
        set_reporter(None)
    assert peak[0] > 1  # several clips in flight at once
    assert [m for _, m in msgs][-1].startswith("4/4 clips") and any(m.startswith("2/4 clips") for _, m in msgs)
    st = client.get(f"/api/projects/{slug}").json()["status"]
    assert all((p.clips_dir / f"{i}.mp4").exists() for i in ids)
    assert st["render"]["state"] in ("needs", "empty")


def test_cancel_queued_jobs(client, monkeypatch):
    import threading

    from songvid.stages import song as song_st

    gate = threading.Event()
    calls = []
    monkeypatch.setattr(song_st, "generate", lambda *a, **k: (calls.append(1), gate.wait(5)))
    slug = client.post("/api/demo").json()["slug"]
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "align"})
    wait(client, slug)
    # cpu lane is single-file: a render holds it while stills and refit queue behind it
    gate2 = threading.Event()
    from songvid.render import compositor
    monkeypatch.setattr(compositor, "render", lambda p, *a, **k: (gate2.wait(5), p.renders_dir / "x.mp4")[1])
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "storyboard", "args": {"heuristic": True}})
    time.sleep(0.5)
    wait(client, slug)
    r = client.post(f"/api/projects/{slug}/jobs", json={"kind": "render", "args": {"preview": True, "start": 0, "end": 5}}).json()["job"]
    time.sleep(0.3)
    q1 = client.post(f"/api/projects/{slug}/jobs", json={"kind": "stills"}).json()["job"]
    q2 = client.post(f"/api/projects/{slug}/jobs", json={"kind": "refit"}).json()["job"]
    assert client.post(f"/api/jobs/{r['id']}/cancel").status_code == 409  # running: refused
    assert client.post(f"/api/jobs/{q1['id']}/cancel").json()["job"]["status"] == "cancelled"
    assert client.post(f"/api/projects/{slug}/jobs/cancel-queued").json()["cancelled"] == [q2["id"]]
    gate2.set()
    d = wait(client, slug)
    st = {j["id"]: j["status"] for j in d["jobs"]}
    assert st[q1["id"]] == st[q2["id"]] == "cancelled" and st[r["id"]] == "done"
    assert any("Cancelled" in h["text"] for h in d["history"])
    # a cancelled job doesn't block running the same step again
    assert client.post(f"/api/projects/{slug}/jobs", json={"kind": "stills"}).status_code == 200
    gate.set()
    wait(client, slug)


def test_render_reports_frame_progress(client):
    from songvid.progress import set_reporter
    from songvid.project import Project
    from songvid.render.compositor import render

    slug = client.post("/api/demo").json()["slug"]
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "align"})
    wait(client, slug)
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "storyboard", "args": {"heuristic": True}})
    time.sleep(0.5)
    wait(client, slug)
    msgs = []
    set_reporter(lambda f, m: msgs.append((f, m)))
    try:
        render(Project(slug), preview=True, start=0, end=4, workers=1)
    finally:
        set_reporter(None)
    assert any("frames" in m for _, m in msgs) and msgs[-1][0] == 1.0
    assert (Project(slug).renders_dir / ".live.jpg").exists()  # the job card's latest-frame thumbnail


def test_job_card_shows_served_model_and_cost(client, monkeypatch):
    from types import SimpleNamespace as NS

    from songvid import llm
    from songvid.stages import song as song_st

    def fake_generate(p, brief, cfg, revise=None):
        # one normal call, then one where a safety fallback served a different model
        llm.record(NS(model="claude-opus-5-5", content=[], usage=NS(input_tokens=6000, output_tokens=3000)), "claude-opus-5-5")
        llm.record(NS(model="claude-opus-4-8", content=[NS(type="fallback")], usage=NS(input_tokens=1000, output_tokens=500)), "claude-opus-5-5")

    monkeypatch.setattr(song_st, "generate", fake_generate)
    slug = client.post("/api/projects", json={"title": "Model Check", "brief": "x"}).json()["slug"]
    client.post(f"/api/projects/{slug}/jobs", json={"kind": "song"})
    d = wait(client, slug)
    msg = d["jobs"][0]["message"]
    assert "claude-opus-5-5 · 6.0k in / 3.0k out · ~$0.08" in msg
    assert "claude-opus-4-8 (fallback from claude-opus-5-5)" in msg
    assert "claude-opus-5-5" in d["history"][0]["text"]
    assert client.get("/api/keys").json()["models"]["claude"] == "claude-opus-5-5"
