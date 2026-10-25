import pytest

from lemming import hooks, paths, persistence


def _project(tmp_path, monkeypatch):
    """Creates an isolated project and returns its tasks file."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text("{}", encoding="utf-8")
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
    assert "testing" in active
    assert "ux" in active
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
    assert active.index("readability") < active.index("testing")
    assert active.index("testing") < active.index("ux")
    assert active.index("ux") < active.index("roadmap")
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


def _custom_hook(tasks_file, filename):
    """Writes a project hook file and returns its directory."""
    local_hooks_dir = hooks.get_local_hooks_dir(tasks_file)
    local_hooks_dir.mkdir(parents=True, exist_ok=True)
    (local_hooks_dir / filename).write_text("prompt", encoding="utf-8")
    return local_hooks_dir


def test_select_hooks_defaults_to_project_config(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    selected = hooks.select_hooks(tasks_file, ["ux", "roadmap"], None)

    assert selected == ["ux", "roadmap"]


def test_select_hooks_task_list_replaces_reviews(tmp_path, monkeypatch):
    """A task picks its reviews but cannot drop the orchestration band."""
    tasks_file = _project(tmp_path, monkeypatch)

    selected = hooks.select_hooks(
        tasks_file, ["ux", "roadmap"], ["testing", "readability"]
    )

    assert selected == ["readability", "testing", "roadmap"]


def test_select_hooks_task_cannot_add_orchestration(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    assert hooks.select_hooks(tasks_file, [], ["roadmap"]) == []


def test_select_hooks_empty_task_list_keeps_orchestration(
    tmp_path, monkeypatch
):
    tasks_file = _project(tmp_path, monkeypatch)

    assert hooks.select_hooks(tasks_file, ["ux", "roadmap"], []) == ["roadmap"]


def test_select_hooks_skips_masked_and_unknown(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)
    local_hooks_dir = hooks.get_local_hooks_dir(tasks_file)
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "ux.md").write_text("", encoding="utf-8")

    selected = hooks.select_hooks(tasks_file, ["ux", "gone", "roadmap"], None)

    assert selected == ["roadmap"]


def test_select_hooks_applies_one_rule_to_custom_hooks(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)
    _custom_hook(tasks_file, "95-deploy.md")
    _custom_hook(tasks_file, "lint.md")

    assert hooks.select_hooks(tasks_file, ["roadmap"], ["lint"]) == [
        "lint",
        "roadmap",
    ]
    assert hooks.select_hooks(tasks_file, ["deploy"], ["deploy"]) == ["deploy"]


def test_parse_reviews_orders_by_priority(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    assert hooks.parse_reviews(["ux, readability"], tasks_file) == [
        "readability",
        "ux",
    ]


def test_parse_reviews_all_excludes_orchestration(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)
    _custom_hook(tasks_file, "lint.md")

    reviews = hooks.parse_reviews(["*"], tasks_file)

    assert "lint" in reviews
    assert "roadmap" not in reviews


def test_parse_reviews_empty_selects_none(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    assert hooks.parse_reviews([""], tasks_file) == []


def test_parse_reviews_rejects_orchestration(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="orchestrates the roadmap"):
        hooks.parse_reviews(["readability,roadmap"], tasks_file)


def test_parse_reviews_rejects_unknown(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="Unknown review 'nope'"):
        hooks.parse_reviews(["nope"], tasks_file)


def _config_hooks(tasks_file):
    return persistence.load_tasks(tasks_file).config.hooks


def test_hooks_default_to_orchestration_only(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    assert _config_hooks(tasks_file) == ["roadmap"]


def test_enable_hooks_adds_to_project_default(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    results = hooks.enable_hooks(["ux", "roadmap"], tasks_file)

    assert results == {"ux": True, "roadmap": False}
    assert sorted(_config_hooks(tasks_file)) == ["roadmap", "ux"]


def test_enable_hooks_is_atomic(tmp_path, monkeypatch):
    """A bad name anywhere in the list means nothing is enabled."""
    tasks_file = _project(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="not found"):
        hooks.enable_hooks(["ux", "nope"], tasks_file)

    assert _config_hooks(tasks_file) == ["roadmap"]


def test_enable_hooks_refuses_masked_hook(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)
    global_hooks_dir = paths.get_global_hooks_dir()
    global_hooks_dir.mkdir(parents=True)
    (global_hooks_dir / "ux.md").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="masked"):
        hooks.enable_hooks(["ux"], tasks_file)

    assert _config_hooks(tasks_file) == ["roadmap"]


def test_disable_hooks_removes_from_project_default(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    results = hooks.disable_hooks(["roadmap", "ux"], tasks_file)

    assert results == {"roadmap": True, "ux": False}
    assert _config_hooks(tasks_file) == []
    # Disabling is a config change, never a mask file
    assert not hooks.get_local_hooks_dir(tasks_file).exists()


def test_disable_hooks_is_atomic(tmp_path, monkeypatch):
    tasks_file = _project(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="not found"):
        hooks.disable_hooks(["roadmap", "nope"], tasks_file)

    assert _config_hooks(tasks_file) == ["roadmap"]


def test_disable_hooks_accepts_stale_config_name(tmp_path, monkeypatch):
    """A deleted custom hook must stay removable from the config."""
    tasks_file = _project(tmp_path, monkeypatch)
    data = persistence.load_tasks(tasks_file)
    data.config.hooks = ["roadmap", "gone"]
    persistence.save_tasks(tasks_file, data)

    assert hooks.disable_hooks(["gone"], tasks_file) == {"gone": True}
    assert _config_hooks(tasks_file) == ["roadmap"]


def test_resolve_hooks_skips_undecodable_files(tmp_path, monkeypatch):
    """A non-UTF8 file must not break discovery for everything else."""
    tasks_file = _project(tmp_path, monkeypatch)
    global_hooks_dir = paths.get_global_hooks_dir()
    global_hooks_dir.mkdir(parents=True)
    (global_hooks_dir / "notes.md").write_bytes(b"\xff\xfe\x00bad")

    active = hooks.list_hooks(tasks_file)

    assert "notes" not in active
    assert "roadmap" in active
