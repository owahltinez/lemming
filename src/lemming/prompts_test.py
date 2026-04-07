from lemming import prompts
from lemming import tasks
from lemming import paths


def test_load_prompt():
    prompt = prompts.load_prompt("taskrunner")
    assert "roadmap" in prompt
    assert "description" in prompt


def test_prepare_prompt(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = tasks.Roadmap(
        context="My context",
        tasks=[
            tasks.Task(
                id="1",
                description="T1",
                status=tasks.TaskStatus.COMPLETED,
                progress=["O1"],
            ),
            tasks.Task(id="2", description="T2", status=tasks.TaskStatus.PENDING),
        ],
    )
    task = data.tasks[1]
    prompt = prompts.prepare_prompt(data, task, tasks_file)
    assert "My context" in prompt
    assert "T2" in prompt
    assert "T1" in prompt
    assert "O1" in prompt


def test_prepare_prompt_with_parent_context(tmp_path):

    root_tasks_file = tmp_path / "root_tasks.yml"
    sub_tasks_file = tmp_path / "sub_tasks.yml"

    # Setup parent task
    parent_task = tasks.Task(
        id="parent123",
        description="Parent Task Description",
        progress=["Parent Outcome 1"],
    )
    root_data = tasks.Roadmap(tasks=[parent_task])
    tasks.save_tasks(root_tasks_file, root_data)

    # Setup child task referencing parent
    child_task = tasks.Task(
        id="child456",
        description="Child Task",
        parent="parent123",
        parent_tasks_file=str(root_tasks_file),
    )
    sub_data = tasks.Roadmap(tasks=[child_task])

    prompt = prompts.prepare_prompt(sub_data, child_task, sub_tasks_file)

    assert "Parent Task Context" in prompt
    assert "Parent Task Description" in prompt
    assert "Parent Outcome 1" in prompt


def test_load_prompt_discovery(tmp_path, monkeypatch):
    """Tests the discovery of hook prompts across different layers."""
    # Setup global hooks dir
    lemming_home = tmp_path / "lemming_home"
    global_hooks_dir = lemming_home / "hooks"
    global_hooks_dir.mkdir(parents=True)
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))

    # Setup project dir
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    local_hooks_dir = project_dir / ".lemming" / "hooks"
    local_hooks_dir.mkdir(parents=True)
    tasks_file = project_dir / "tasks.yml"
    tasks_file.write_text("tasks: []")

    # 1. Test local hook
    (local_hooks_dir / "myhook.md").write_text("local content")
    assert prompts.load_prompt("myhook", tasks_file) == "local content"

    # 2. Test global hook (when local doesn't exist)
    (global_hooks_dir / "globalhook.md").write_text("global content")
    assert prompts.load_prompt("globalhook", tasks_file) == "global content"

    # 3. Test precedence: local > global
    (global_hooks_dir / "myhook.md").write_text("global content")
    assert prompts.load_prompt("myhook", tasks_file) == "local content"

    # 4. Test built-in (fallback)
    assert "You are a roadmap orchestrator" in prompts.load_prompt(
        "roadmap", tasks_file
    )


def test_prepare_hook_prompt_substitution(tmp_path, monkeypatch):
    # Setup paths to avoid polluting real global/home
    lemming_home = tmp_path / "lemming_home"
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))

    tasks_file = tmp_path / "tasks.yml"
    data = tasks.Roadmap(
        context="Project Context",
        tasks=[
            tasks.Task(
                id="task1",
                description="Task 1",
                status=tasks.TaskStatus.COMPLETED,
                progress=["Done"],
            ),
            tasks.Task(
                id="task2", description="Task 2", status=tasks.TaskStatus.IN_PROGRESS
            ),
        ],
    )
    finished_task = data.tasks[0]

    # Create a mock hook prompt with all placeholders
    local_hooks_dir = tmp_path / ".lemming" / "hooks"
    local_hooks_dir.mkdir(parents=True)
    hook_prompt_file = local_hooks_dir / "test-hook.md"
    hook_prompt_file.write_text("""
Roadmap: {{roadmap}}
Finished Task: {{finished_task}}
ID: {{finished_task_id}}
File Name: {{tasks_file_name}}
File Path: {{tasks_file_path}}
""")

    # Mock log file
    log_file = paths.get_log_file(tasks_file, finished_task.id)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("line 1\nline 2\nline 3")

    prompt = prompts.prepare_hook_prompt("test-hook", data, finished_task, tasks_file)

    # Verify roadmap substitution
    assert "Roadmap: ## Project Context" in prompt
    assert "**[COMPLETED] (task1) Task 1**" in prompt
    assert "  - Done" in prompt
    assert "- [IN PROGRESS] (task2) Task 2" in prompt

    # Verify finished task substitution
    assert "Finished Task: Task ID: task1" in prompt
    assert "Description: Task 1" in prompt
    assert "Result: completed" in prompt
    assert "Progress recorded during this attempt:\n- Done" in prompt

    # Verify log inclusion
    assert "Execution log of THIS task" in prompt
    assert "line 1\nline 2\nline 3" in prompt


def test_prepare_prompt_local_override(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    project_dir = tmp_path
    local_hooks_dir = project_dir / ".lemming" / "hooks"
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "taskrunner.md").write_text("LOCAL OVERRIDE {{description}}")

    data = tasks.Roadmap(
        tasks=[
            tasks.Task(id="1", description="My Task"),
        ],
    )
    task = data.tasks[0]
    prompt = prompts.prepare_prompt(data, task, tasks_file)
    assert "LOCAL OVERRIDE My Task" in prompt


def test_prepare_hook_prompt_filters_command_noise(tmp_path, monkeypatch):
    # Setup paths to avoid polluting real global/home
    lemming_home = tmp_path / "lemming_home"
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))

    tasks_file = tmp_path / "tasks.yml"
    data = tasks.Roadmap(
        context="Project Context",
        tasks=[
            tasks.Task(
                id="task1",
                description="Task 1",
                status=tasks.TaskStatus.COMPLETED,
            ),
        ],
    )
    finished_task = data.tasks[0]

    # Create a mock hook prompt with all placeholders
    local_hooks_dir = tmp_path / ".lemming" / "hooks"
    local_hooks_dir.mkdir(parents=True)
    hook_prompt_file = local_hooks_dir / "test-hook.md"
    hook_prompt_file.write_text("Log: {{finished_task}}")

    # Mock log file with a long Command: line
    log_file = paths.get_log_file(tasks_file, finished_task.id)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(
        "Command: gemini --prompt \"HUGE PROMPT WITH 'QUOTES'\" ...\nReal output from AI\n"
    )

    prompt = prompts.prepare_hook_prompt("test-hook", data, finished_task, tasks_file)

    # Verify Command: line is filtered out
    assert "HUGE PROMPT" not in prompt
    assert "Real output from AI" in prompt
    assert "Execution log of THIS task" in prompt


def test_ensure_hooks_symlinked(tmp_path, monkeypatch):
    # Setup mock lemming home
    lemming_home = tmp_path / "lemming_home"
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))

    global_hooks_dir = paths.get_global_hooks_dir()
    assert not global_hooks_dir.exists()

    # Run ensure_hooks_symlinked
    prompts.ensure_hooks_symlinked()

    assert global_hooks_dir.exists()
    assert (global_hooks_dir / "roadmap.md").is_symlink()
    assert (global_hooks_dir / "readability.md").is_symlink()

    # Check if we can load it
    content = prompts.load_prompt("roadmap")
    assert "roadmap orchestrator" in content.lower()

    content = prompts.load_prompt("readability")
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
    hooks = prompts.list_hooks(tasks_file)

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
    content = prompts.load_prompt("roadmap", tasks_file)
    assert content == "project roadmap"

    # Remove project override
    (local_hooks_dir / "roadmap.md").unlink()
    content = prompts.load_prompt("roadmap", tasks_file)
    assert content == "global roadmap"


def test_prepare_prompt_time_limit_section(tmp_path):
    """Verifies the time limit section is injected when time_limit > 0."""
    tasks_file = tmp_path / "tasks.yml"
    data = tasks.Roadmap(
        tasks=[tasks.Task(id="1", description="T1")],
    )
    task = data.tasks[0]

    # With time limit (60 minutes)
    prompt = prompts.prepare_prompt(data, task, tasks_file, time_limit=60)
    assert "## Time Limit" in prompt
    assert "60 minutes" in prompt
    assert "Record progress early" in prompt
    assert "subagents" in prompt

    # Without time limit
    prompt_no_limit = prompts.prepare_prompt(data, task, tasks_file, time_limit=0)
    assert "## Time Limit" not in prompt_no_limit


def test_prepare_prompt_time_limit_custom(tmp_path):
    """Verifies the time limit section uses the correct minute value."""
    tasks_file = tmp_path / "tasks.yml"
    data = tasks.Roadmap(
        tasks=[tasks.Task(id="1", description="T1")],
    )
    task = data.tasks[0]

    prompt = prompts.prepare_prompt(data, task, tasks_file, time_limit=30)
    assert "30 minutes" in prompt


def test_prepare_hook_prompt_shows_failed_for_exhausted_task(tmp_path, monkeypatch):
    """A task with requested_status=FAILED (during hook execution) should show
    Result: failed and [FAILED] in the roadmap, not in_progress."""
    lemming_home = tmp_path / "lemming_home"
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))

    tasks_file = tmp_path / "tasks.yml"
    # Simulate the state during hook execution after retry exhaustion:
    # status=IN_PROGRESS, requested_status=FAILED
    failed_task = tasks.Task(
        id="task1",
        description="Flaky task",
        status=tasks.TaskStatus.IN_PROGRESS,
        requested_status=tasks.TaskStatus.FAILED,
        attempts=3,
        progress=["Task killed: time limit of 60 minutes reached."],
    )
    data = tasks.Roadmap(
        context="Test",
        tasks=[
            failed_task,
            tasks.Task(id="task2", description="Next task"),
        ],
    )

    local_hooks_dir = tmp_path / ".lemming" / "hooks"
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "test-hook.md").write_text(
        "Roadmap: {{roadmap}}\nFinished: {{finished_task}}"
    )

    prompt = prompts.prepare_hook_prompt("test-hook", data, failed_task, tasks_file)

    # The finished task section must show "failed", not "in_progress"
    assert "Result: failed" in prompt
    # The roadmap overview must show [FAILED], not [IN PROGRESS]
    assert "[FAILED - 3/3 attempt(s)]" in prompt
    assert "[PENDING]" in prompt


def test_list_hooks_roadmap_is_last(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.touch()

    # Create project hooks that would alphabetically come before and after 'roadmap'
    local_hooks_dir = tmp_path / ".lemming" / "hooks"
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "z_hook.md").write_text("z", encoding="utf-8")
    (local_hooks_dir / "a_hook.md").write_text("a", encoding="utf-8")

    hooks = prompts.list_hooks(tasks_file)

    assert "roadmap" in hooks
    assert "z_hook" in hooks
    assert "a_hook" in hooks
    # Even though z_hook is alphabetically last, roadmap should be moved to the end
    assert hooks[-1] == "roadmap"


def test_format_roadmap_with_finalizing_task():
    from lemming import models

    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Done",
                status=models.TaskStatus.COMPLETED,
                progress=["a"],
            ),
            models.Task(
                id="2",
                description="Finalizing",
                status=models.TaskStatus.IN_PROGRESS,
                requested_status=models.TaskStatus.COMPLETED,
                progress=["b"],
            ),
        ]
    )

    output = prompts._format_roadmap(data)
    assert "[COMPLETED] (2)" in output
    assert "- b" in output


def test_format_roadmap_with_finalizing_task():
    from lemming import models
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1", 
                description="Done", 
                status=models.TaskStatus.COMPLETED,
                progress=["a"]
            ),
            models.Task(
                id="2", 
                description="Finalizing", 
                status=models.TaskStatus.IN_PROGRESS,
                requested_status=models.TaskStatus.COMPLETED,
                progress=["b"]
            ),
        ]
    )
    
    output = prompts._format_roadmap(data)
    assert "[COMPLETED] (2)" in output
    assert "- b" in output
