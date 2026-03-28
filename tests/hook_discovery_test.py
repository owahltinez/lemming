from lemming import runner, paths

def test_ensure_hooks_symlinked(tmp_path, monkeypatch):
    # Setup mock lemming home
    lemming_home = tmp_path / "lemming_home"
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))
    
    global_hooks_dir = paths.get_global_hooks_dir()
    assert not global_hooks_dir.exists()
    
    # Run ensure_hooks_symlinked
    runner.ensure_hooks_symlinked()
    
    assert global_hooks_dir.exists()
    assert (global_hooks_dir / "roadmap.md").is_symlink()
    assert (global_hooks_dir / "readability.md").is_symlink()
    
    # Check if we can load it
    content = runner.load_prompt("roadmap")
    assert "roadmap orchestrator" in content.lower()
    
    content = runner.load_prompt("readability")
    assert "google style guide" in content.lower()

def test_list_hooks_includes_all(tmp_path, monkeypatch):
    # Setup mock lemming home
    lemming_home = tmp_path / "lemming_home"
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))
    
    # Project hooks
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    local_hooks_dir = project_dir / ".lemming" / "hooks"
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "custom_hook.md").write_text("custom", encoding="utf-8")
    
    tasks_file = project_dir / "tasks.yml"
    tasks_file.touch()
    
    # Run list_hooks
    hooks = runner.list_hooks(tasks_file)
    
    assert "roadmap" in hooks
    assert "readability" in hooks
    assert "custom_hook" in hooks

def test_hook_override_precedence(tmp_path, monkeypatch):
    # Setup mock lemming home
    lemming_home = tmp_path / "lemming_home"
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))
    
    # Global override
    global_hooks_dir = paths.get_global_hooks_dir()
    global_hooks_dir.mkdir(parents=True)
    (global_hooks_dir / "roadmap.md").write_text("global roadmap", encoding="utf-8")
    
    # Project override
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    local_hooks_dir = project_dir / ".lemming" / "hooks"
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "roadmap.md").write_text("project roadmap", encoding="utf-8")
    
    tasks_file = project_dir / "tasks.yml"
    tasks_file.touch()
    
    # Check precedence
    content = runner.load_prompt("roadmap", tasks_file)
    assert content == "project roadmap"
    
    # Remove project override
    (local_hooks_dir / "roadmap.md").unlink()
    content = runner.load_prompt("roadmap", tasks_file)
    assert content == "global roadmap"
