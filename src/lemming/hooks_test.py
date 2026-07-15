import pytest

from lemming import hooks, paths


def _project(tmp_path, monkeypatch):
    """Creates an isolated project and returns its tasks file."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.touch()
    return tasks_file


def test_parse_hook_stem():
    assert hooks.parse_hook_stem("90-roadmap") == (90, "roadmap")
    assert hooks.parse_hook_stem("5-my-hook") == (5, "my-hook")
    assert hooks.parse_hook_stem("lint") == (
        hooks.DEFAULT_HOOK_PRIORITY,
        "lint",
    )


def test_list_hooks_includes_all(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)
    local_hooks_dir = hooks.get_local_hooks_dir(tasks_file)
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "custom_hook.md").write_text("custom", encoding="utf-8")

    active = hooks.list_hooks(tasks_file)

    assert "roadmap" in active
    assert "readability" in active
    assert "custom_hook" in active


def test_list_hooks_orders_by_priority(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    # A low-priority hook and an unprefixed one (defaults to 50)
    local_hooks_dir = hooks.get_local_hooks_dir(tasks_file)
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "10-early.md").write_text("e", encoding="utf-8")
    (local_hooks_dir / "zz_hook.md").write_text("z", encoding="utf-8")

    active = hooks.list_hooks(tasks_file)

    # Numeric prefix orders execution; built-in roadmap (90) runs last
    assert active[0] == "early"
    assert active[-1] == "roadmap"
    assert active.index("zz_hook") < active.index("roadmap")


def test_empty_file_masks_hook(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    # An empty project file masks (disables) the built-in hook
    local_hooks_dir = hooks.get_local_hooks_dir(tasks_file)
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "roadmap.md").write_text("", encoding="utf-8")

    assert "roadmap" not in hooks.list_hooks(tasks_file)

    # The masked hook is still visible to resolution, flagged as masked
    resolved = {h.name: h for h in hooks.resolve_hooks(tasks_file)}
    assert resolved["roadmap"].masked


def test_hook_override_sets_priority(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    # A project override wins by logical name and its prefix sets the order
    local_hooks_dir = hooks.get_local_hooks_dir(tasks_file)
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "10-roadmap.md").write_text("custom", encoding="utf-8")

    assert hooks.list_hooks(tasks_file)[0] == "roadmap"
    assert hooks.get_hook_priority("roadmap", tasks_file) == 10


def test_resolve_hooks_skips_dangling_symlinks(tmp_path, monkeypatch):
    """Stale symlinks (e.g. from the removed 'hooks install') are ignored."""
    tasks_file = _project(tmp_path, monkeypatch)
    global_hooks_dir = paths.get_global_hooks_dir()
    global_hooks_dir.mkdir(parents=True)
    (global_hooks_dir / "roadmap.md").symlink_to(tmp_path / "gone.md")

    # The dangling symlink is skipped; the built-in remains active
    resolved = {h.name: h for h in hooks.resolve_hooks(tasks_file)}
    assert resolved["roadmap"].source == "built-in"
    assert not resolved["roadmap"].masked


def test_disable_hooks_keeps_priority_in_mask(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    results = hooks.disable_hooks(["roadmap"], tasks_file)

    # The mask filename carries the hook's priority so listings still
    # report the priority it would run at if re-enabled
    mask = results["roadmap"]
    assert mask.name == "90-roadmap.md"
    assert mask.read_text(encoding="utf-8") == ""

    resolved = {h.name: h for h in hooks.resolve_hooks(tasks_file)}
    assert resolved["roadmap"].masked
    assert resolved["roadmap"].priority == 90


def test_disable_hooks_already_disabled(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    hooks.disable_hooks(["roadmap"], tasks_file)
    results = hooks.disable_hooks(["roadmap"], tasks_file)

    assert results == {"roadmap": None}


def test_disable_hooks_is_atomic(tmp_path, monkeypatch):
    """A bad name anywhere in the list means no mask is written at all."""
    tasks_file = _project(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="not found"):
        hooks.disable_hooks(["readability", "nope"], tasks_file)

    assert "readability" in hooks.list_hooks(tasks_file)
    assert not hooks.get_local_hooks_dir(tasks_file).exists()


def test_disable_hooks_refuses_project_override(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)
    local_hooks_dir = hooks.get_local_hooks_dir(tasks_file)
    local_hooks_dir.mkdir(parents=True)
    override = local_hooks_dir / "roadmap.md"
    override.write_text("custom prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="project override"):
        hooks.disable_hooks(["roadmap"], tasks_file)

    # The override must not be clobbered
    assert override.read_text(encoding="utf-8") == "custom prompt"


def test_enable_hooks_removes_all_matching_masks(tmp_path, monkeypatch):
    """Both prefixed and unprefixed masks for a logical name are removed."""
    tasks_file = _project(tmp_path, monkeypatch)
    local_hooks_dir = hooks.get_local_hooks_dir(tasks_file)
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "roadmap.md").write_text("", encoding="utf-8")
    (local_hooks_dir / "90-roadmap.md").write_text("", encoding="utf-8")

    results = hooks.enable_hooks(["roadmap"], tasks_file)

    assert results == {"roadmap": True}
    assert list(local_hooks_dir.glob("*.md")) == []
    assert "roadmap" in hooks.list_hooks(tasks_file)


def test_enable_hooks_already_enabled(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    results = hooks.enable_hooks(["roadmap"], tasks_file)

    assert results == {"roadmap": False}


def test_enable_hooks_is_atomic(tmp_path, monkeypatch):
    """A bad name anywhere in the list means no mask is removed at all."""
    tasks_file = _project(tmp_path, monkeypatch)
    hooks.disable_hooks(["roadmap"], tasks_file)

    with pytest.raises(ValueError, match="not found"):
        hooks.enable_hooks(["roadmap", "nope"], tasks_file)

    assert "roadmap" not in hooks.list_hooks(tasks_file)


def test_enable_hooks_refuses_project_override(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)
    local_hooks_dir = hooks.get_local_hooks_dir(tasks_file)
    local_hooks_dir.mkdir(parents=True)
    override = local_hooks_dir / "roadmap.md"
    override.write_text("custom prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="project override"):
        hooks.enable_hooks(["roadmap"], tasks_file)

    assert override.exists()


def test_enable_hooks_masked_outside_project(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)
    global_hooks_dir = paths.get_global_hooks_dir()
    global_hooks_dir.mkdir(parents=True)
    (global_hooks_dir / "roadmap.md").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the project"):
        hooks.enable_hooks(["roadmap"], tasks_file)


def test_resolve_hooks_skips_undecodable_files(tmp_path, monkeypatch):
    """A non-UTF8 file must not break discovery for everything else."""
    tasks_file = _project(tmp_path, monkeypatch)
    global_hooks_dir = paths.get_global_hooks_dir()
    global_hooks_dir.mkdir(parents=True)
    (global_hooks_dir / "notes.md").write_bytes(b"\xff\xfe\x00bad")

    active = hooks.list_hooks(tasks_file)

    assert "notes" not in active
    assert "roadmap" in active


def test_enable_hooks_refuses_undecodable_file(tmp_path, monkeypatch):
    """A non-UTF8 project file is not a mask; never delete it."""
    tasks_file = _project(tmp_path, monkeypatch)
    local_hooks_dir = hooks.get_local_hooks_dir(tasks_file)
    local_hooks_dir.mkdir(parents=True)
    weird = local_hooks_dir / "roadmap.md"
    weird.write_bytes(b"\xff\xfe\x00bad")

    with pytest.raises(ValueError, match="project override"):
        hooks.enable_hooks(["roadmap"], tasks_file)

    assert weird.exists()
