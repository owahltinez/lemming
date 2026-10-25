from lemming import persistence
from lemming.api import main


def test_list_hooks(client, test_tasks, monkeypatch, tmp_path):
    """The /api/hooks endpoint returns resolved hooks in execution order."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))

    response = client.get("/api/hooks")

    assert response.status_code == 200
    payload = response.json()
    hooks = {h["name"]: h for h in payload}
    assert hooks["roadmap"]["priority"] == 90
    assert hooks["roadmap"]["runs_on_failure"] is True
    assert hooks["roadmap"]["masked"] is False
    assert hooks["readability"]["source"] == "built-in"
    assert hooks["readability"]["runs_on_failure"] is False
    assert hooks["ux"]["priority"] == 70
    assert hooks["ux"]["source"] == "built-in"
    assert hooks["ux"]["runs_on_failure"] is False

    # Hooks are sorted by ascending priority
    priorities = [h["priority"] for h in payload]
    assert priorities == sorted(priorities)


def test_list_hooks_with_project(client, test_tasks, monkeypatch, tmp_path):
    """The /api/hooks endpoint works with a specified project."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    root = main.app.state.root
    project_dir = root / "project1"
    project_dir.mkdir()

    response = client.get("/api/hooks", params={"project": "project1"})

    assert response.status_code == 200
    assert "roadmap" in {h["name"] for h in response.json()}


def test_toggle_hook(client, test_tasks, monkeypatch, tmp_path):
    """Toggling a hook edits the project's default set, not mask files."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))

    response = client.get("/api/hooks")
    hooks = {h["name"]: h for h in response.json()}
    assert hooks["roadmap"]["enabled"] is True
    assert hooks["ux"]["enabled"] is False

    response = client.post("/api/hooks", json={"name": "ux", "enabled": True})
    assert response.status_code == 200
    hooks = {h["name"]: h for h in response.json()}
    assert hooks["ux"]["enabled"] is True

    response = client.post(
        "/api/hooks", json={"name": "roadmap", "enabled": False}
    )
    assert response.status_code == 200
    hooks = {h["name"]: h for h in response.json()}
    assert hooks["roadmap"]["enabled"] is False
    assert hooks["roadmap"]["masked"] is False
    assert persistence.load_tasks(test_tasks).config.hooks == ["ux"]
    assert not (test_tasks.parent / ".lemming").exists()


def test_toggle_unknown_hook(client, test_tasks, monkeypatch, tmp_path):
    """Toggling an unknown hook returns a 400 error."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))

    response = client.post(
        "/api/hooks", json={"name": "does-not-exist", "enabled": False}
    )

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]
