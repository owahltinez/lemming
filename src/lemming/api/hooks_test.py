import lemming.api


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

    # Hooks are sorted by ascending priority
    priorities = [h["priority"] for h in payload]
    assert priorities == sorted(priorities)


def test_list_hooks_with_project(client, test_tasks, monkeypatch, tmp_path):
    """The /api/hooks endpoint works with a specified project."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    root = lemming.api.app.state.root
    project_dir = root / "project1"
    project_dir.mkdir()
    tasks_file = project_dir / "tasks.yml"
    tasks_file.touch()

    response = client.get("/api/hooks", params={"project": "project1"})

    assert response.status_code == 200
    assert "roadmap" in {h["name"] for h in response.json()}


def test_toggle_hook(client, test_tasks, monkeypatch, tmp_path):
    """Disabling a hook creates a project mask; enabling removes it."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    # The mask filename keeps the hook's priority (roadmap is 90)
    mask = test_tasks.parent / ".lemming" / "hooks" / "90-roadmap.md"

    # Disable: the response reflects the mask and the file exists
    response = client.post(
        "/api/hooks", json={"name": "roadmap", "enabled": False}
    )
    assert response.status_code == 200
    hooks = {h["name"]: h for h in response.json()}
    assert hooks["roadmap"]["masked"] is True
    assert mask.exists()
    assert mask.read_text(encoding="utf-8") == ""

    # Enable: the mask is removed
    response = client.post(
        "/api/hooks", json={"name": "roadmap", "enabled": True}
    )
    assert response.status_code == 200
    hooks = {h["name"]: h for h in response.json()}
    assert hooks["roadmap"]["masked"] is False
    assert not mask.exists()


def test_toggle_unknown_hook(client, test_tasks, monkeypatch, tmp_path):
    """Toggling an unknown hook returns a 400 error."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))

    response = client.post(
        "/api/hooks", json={"name": "does-not-exist", "enabled": False}
    )

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


def test_toggle_hook_refuses_project_override(
    client, test_tasks, monkeypatch, tmp_path
):
    """Disabling a hook with a project override must not clobber it."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    local_hooks_dir = test_tasks.parent / ".lemming" / "hooks"
    local_hooks_dir.mkdir(parents=True)
    override = local_hooks_dir / "roadmap.md"
    override.write_text("custom prompt", encoding="utf-8")

    response = client.post(
        "/api/hooks", json={"name": "roadmap", "enabled": False}
    )

    assert response.status_code == 400
    assert override.read_text(encoding="utf-8") == "custom prompt"
