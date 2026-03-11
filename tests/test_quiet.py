import pathlib
from click.testing import CliRunner
from lemming.main import cli, load_tasks, save_tasks

def test_verbose_info(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = {
        "context": "Some context",
        "tasks": [
            {"id": "1", "description": "task 1", "status": "completed", "attempts": 1, "lessons": []},
            {"id": "2", "description": "task 2", "status": "pending", "attempts": 0, "lessons": []},
        ]
    }
    save_tasks(tasks_file, data)
    
    runner = CliRunner()
    # Test without verbose (should be quiet by default)
    result = runner.invoke(cli, ["--tasks-file", str(tasks_file), "status"])
    assert "=== Project Context ===" not in result.output
    assert "task 1" not in result.output
    assert "task 2" in result.output
    assert "(1 completed tasks hidden)" in result.output
    
    # Test with verbose
    result_v = runner.invoke(cli, ["--verbose", "--tasks-file", str(tasks_file), "status"])
    assert "=== Project Context ===" in result_v.output
    assert "task 1" in result_v.output
    assert "task 2" in result_v.output

def test_verbose_add(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    runner = CliRunner()
    # Default is quiet (just ID)
    result = runner.invoke(cli, ["--tasks-file", str(tasks_file), "add", "new task"])
    assert len(result.output.strip()) == 8 # hex ID of length 8
    assert "Added task" not in result.output

    # Verbose shows the message
    result_v = runner.invoke(cli, ["--verbose", "--tasks-file", str(tasks_file), "add", "another task"])
    assert "Added task" in result_v.output

def test_run_default_quiet(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = {
        "context": "Context",
        "tasks": [{"id": "123", "description": "Work", "status": "pending", "attempts": 0, "lessons": []}]
    }
    save_tasks(tasks_file, data)
    
    runner = CliRunner()
    # Run is quiet by default, but should still report success
    result = runner.invoke(cli, ["--tasks-file", str(tasks_file), "run", "--agent", "true", "--max-attempts", "1"])
    assert "[123] Attempt 1/1: Work" in result.output
    assert "--- Task 123" not in result.output
    # It should not have completed because 'true' doesn't call lemming complete
    assert "Task completed successfully!" not in result.output
    assert "All tasks completed!" not in result.output

def test_run_success_reported_in_quiet(tmp_path):
    # This test needs an agent that calls 'lemming complete'
    # But CliRunner might be tricky with subprocesses calling the same app
    pass

def test_run_verbose_global(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = {
        "context": "Context",
        "tasks": [{"id": "123", "description": "Work", "status": "pending", "attempts": 0, "lessons": []}]
    }
    save_tasks(tasks_file, data)
    
    runner = CliRunner()
    # Run with global verbose shows more
    result = runner.invoke(cli, ["--verbose", "--tasks-file", str(tasks_file), "run", "--agent", "true", "--max-attempts", "1"])
    assert "--- Task 123" in result.output
    assert "=== Agent Prompt ===" in result.output
