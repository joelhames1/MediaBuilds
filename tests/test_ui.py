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
