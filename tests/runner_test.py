from lemming import runner
from lemming import tasks
from lemming import paths


def test_load_prompt():
    prompt = runner.load_prompt("taskrunner")
    assert "roadmap" in prompt
    assert "description" in prompt


def test_build_runner_command_gemini():
    cmd = runner.build_runner_command("gemini", "my prompt", yolo=True)
    assert "--yolo" in cmd
    assert "--prompt" in cmd
    assert "my prompt" in cmd


def test_build_runner_command_aider():
    cmd = runner.build_runner_command("aider", "my prompt", yolo=True)
    assert "--yes" in cmd
    assert "--message" in cmd


def test_build_runner_command_with_flags_in_name():
    cmd = runner.build_runner_command(
        "claude-corp -- --output-format=stream-json", "my prompt", yolo=True
    )
    assert cmd[0] == "claude-corp"
    assert "--" in cmd
    assert "--output-format=stream-json" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--print" in cmd
    assert "my prompt" in cmd


def test_build_runner_command_with_quoted_flags_in_name():
    cmd = runner.build_runner_command(
        'my-runner --model "gpt 4"', "my prompt", yolo=True, no_defaults=True
    )
    assert cmd[0] == "my-runner"
    assert "--model" in cmd
    assert "gpt 4" in cmd


def test_build_runner_command_template_basic():
    cmd = runner.build_runner_command(
        "my-tool --input={{prompt}} --json", "hello world", yolo=True
    )
    assert cmd == ["my-tool", "--input=hello world", "--json"]


def test_build_runner_command_template_standalone_placeholder():
    cmd = runner.build_runner_command(
        "my-tool --flag {{prompt}}", "hello world", yolo=True
    )
    assert cmd == ["my-tool", "--flag", "hello world"]


def test_build_runner_command_template_with_runner_args():
    cmd = runner.build_runner_command(
        "my-tool {{prompt}}", "hello", yolo=True, runner_args=("--extra",)
    )
    assert cmd == ["my-tool", "hello", "--extra"]


def test_build_runner_command_template_ignores_defaults():
    # Even though runner starts with "gemini", template mode should not
    # inject --yolo etc.
    cmd = runner.build_runner_command(
        "gemini --custom {{prompt}}", "do stuff", yolo=True
    )
    assert "--yolo" not in cmd
    assert "--no-sandbox" not in cmd
    assert cmd == ["gemini", "--custom", "do stuff"]


def test_build_runner_command_template_prompt_in_flag_value():
    cmd = runner.build_runner_command(
        "my-tool --msg={{prompt}} --verbose", "hi there", yolo=True
    )
    assert cmd == ["my-tool", "--msg=hi there", "--verbose"]


def test_prepare_prompt(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = tasks.Roadmap(
        context="My context",
        tasks=[
            tasks.Task(
                id="1",
                description="T1",
                status=tasks.TaskStatus.COMPLETED,
                outcomes=["O1"],
            ),
            tasks.Task(id="2", description="T2", status=tasks.TaskStatus.PENDING),
        ],
    )
    task = data.tasks[1]
    prompt = runner.prepare_prompt(data, task, tasks_file)
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
        outcomes=["Parent Outcome 1"],
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

    prompt = runner.prepare_prompt(sub_data, child_task, sub_tasks_file)

    assert "Parent Task Context" in prompt
    assert "Parent Task Description" in prompt
    assert "Parent Outcome 1" in prompt


def test_pretty_quote():
    # Test fallback to shlex
    assert runner._pretty_quote("simple") == "simple"
    assert runner._pretty_quote("has space") == "'has space'"

    # Test readable double quotes for single quotes
    assert runner._pretty_quote("has 'single' quotes") == "\"has 'single' quotes\""
    assert runner._pretty_quote("You are 'Lemming'") == "\"You are 'Lemming'\""

    # Test string with double quotes (should fall back to single quotes)
    assert runner._pretty_quote('has "double" quotes') == "'has \"double\" quotes'"

    # Test escaping specials inside double quotes
    assert (
        runner._pretty_quote("has 'single' and \"double\" quotes")
        == '"has \'single\' and \\"double\\" quotes"'
    )

    # Test exclamation mark fallback
    assert runner._pretty_quote("Hello!") == "'Hello!'"

    assert runner._pretty_quote("has 'single' and !") == (
        "'has '\"'\"'single'\"'\"' and !'"
    )


def test_shlex_join_pretty():
    cmd = [
        "example-cli",
        "--dangerously-skip-permissions",
        "--print",
        "You are 'Lemming'",
    ]
    joined = runner._shlex_join_pretty(cmd)
    assert (
        joined
        == "example-cli --dangerously-skip-permissions --print \"You are 'Lemming'\""
    )


def test_load_prompt_discovery(tmp_path, monkeypatch):
    """Tests the discovery of hook prompts across different layers.

    Scenarios tested:
    1. Local project hook.
    2. Global hook (fallback from local).
    3. Precedence: local > global.
    4. Built-in hook (fallback from global).
    5. Precedence: global > built-in.
    6. Empty global hook (fallback to built-in).
    """
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
    assert runner.load_prompt("myhook", tasks_file) == "local content"

    # 2. Test global hook (when local doesn't exist)
    (global_hooks_dir / "globalhook.md").write_text("global content")
    assert runner.load_prompt("globalhook", tasks_file) == "global content"

    # 3. Test precedence: local > global
    (global_hooks_dir / "myhook.md").write_text("global content")
    assert runner.load_prompt("myhook", tasks_file) == "local content"

    # 4. Test built-in (fallback)
    # "roadmap" is a built-in hook in prompts/hooks/roadmap.md
    assert "You are a roadmap orchestrator" in runner.load_prompt("roadmap", tasks_file)

    # 5. Test global > built-in
    (global_hooks_dir / "roadmap.md").write_text("custom roadmap")
    assert runner.load_prompt("roadmap", tasks_file) == "custom roadmap"

    # 6. Test empty global hook (should fallback to built-in)
    (global_hooks_dir / "readability.md").write_text("   ")
    assert "senior code reviewer" in runner.load_prompt("readability", tasks_file)


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
                outcomes=["Done"],
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

    prompt = runner.prepare_hook_prompt("test-hook", data, finished_task, tasks_file)

    # Verify roadmap substitution
    assert "Roadmap: ## Project Context" in prompt
    assert "- [COMPLETED] (task1) Task 1" in prompt
    assert "  - Done" in prompt
    assert "- [IN PROGRESS] (task2) Task 2" in prompt

    # Verify finished task substitution
    assert "Finished Task: Task ID: task1" in prompt
    assert "Description: Task 1" in prompt
    assert "Result: completed" in prompt
    assert "Outcomes:\n- Done" in prompt

    # Verify log inclusion
    assert "Execution log of THIS task" in prompt
    assert "line 1\nline 2\nline 3" in prompt

    # Verify other placeholders
    assert "ID: task1" in prompt
    assert f"File Name: {tasks_file.name}" in prompt
    assert f"File Path: {runner._pretty_quote(str(tasks_file))}" in prompt


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
    prompt = runner.prepare_prompt(data, task, tasks_file)
    assert "LOCAL OVERRIDE My Task" in prompt
