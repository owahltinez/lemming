import subprocess
import time
import click.testing
import unittest.mock

from lemming import main
from lemming import tasks
from lemming import paths


def test_cancel_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    runner = click.testing.CliRunner()

    # 1. Add a task
    runner.invoke(main.cli, ["--tasks-file", str(tasks_file), "add", "Task to cancel"])
    data = tasks.load_tasks(tasks_file)
    task_id = data.tasks[0].id

    # 2. Mock it as in_progress with a real-ish PID (current process for simplicity of testing the mark/unmark)
    # But wait, cancel_task actually tries to kill it.
    # Let's start a sleep process.
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    pid = proc.pid

    data = tasks.load_tasks(tasks_file)
    data.tasks[0].status = tasks.TaskStatus.IN_PROGRESS
    data.tasks[0].pid = pid
    data.tasks[0].last_heartbeat = time.time()
    tasks.save_tasks(tasks_file, data)

    # 3. Cancel it
    result = runner.invoke(
        main.cli, ["--tasks-file", str(tasks_file), "cancel", task_id]
    )
    assert result.exit_code == 0
    assert f"Task {task_id} cancelled" in result.output

    # 4. Verify status is pending and PID is gone
    data = tasks.load_tasks(tasks_file)
    assert data.tasks[0].status == tasks.TaskStatus.PENDING
    assert data.tasks[0].pid is None

    # 5. Verify process is killed
    time.sleep(0.1)
    assert proc.poll() is not None  # Process should be terminated


def test_cancel_task_stops_loop(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    runner = click.testing.CliRunner()

    # 1. Add a task that sleeps
    runner.invoke(main.cli, ["--tasks-file", str(tasks_file), "add", "Task to cancel"])
    data = tasks.load_tasks(tasks_file)
    task_id = data.tasks[0].id

    # 2. Start a mock loop by writing a fake PID to the lock file
    # We'll start a real subprocess that sleeps so we have a valid PID to kill
    loop_proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    loop_pid = loop_proc.pid

    # We need to manually write the loop lock since we're mocking the loop
    project_dir = paths.get_project_dir(tasks_file)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".lemming_loop.lock").write_text(str(loop_pid))

    # Mock the task as in_progress
    task_proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    task_pid = task_proc.pid

    data = tasks.load_tasks(tasks_file)
    data.tasks[0].status = tasks.TaskStatus.IN_PROGRESS
    data.tasks[0].pid = task_pid
    data.tasks[0].last_heartbeat = time.time()
    tasks.save_tasks(tasks_file, data)

    # 3. Cancel it
    result = runner.invoke(
        main.cli, ["--tasks-file", str(tasks_file), "cancel", task_id]
    )
    assert result.exit_code == 0
    assert f"Task {task_id} cancelled" in result.output

    # 4. Verify both processes are killed
    time.sleep(0.5)
    assert task_proc.poll() is not None
    assert loop_proc.poll() is not None

    # 5. Verify loop is reported as not running
    assert not tasks.is_loop_running(tasks_file)


def test_cancel_not_found(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    runner = click.testing.CliRunner()
    result = runner.invoke(
        main.cli, ["--tasks-file", str(tasks_file), "cancel", "nonexistent"]
    )
    assert result.exit_code == 1
    assert "Error: Task nonexistent not found" in result.output


@unittest.mock.patch("subprocess.Popen")
def test_no_hooks_on_cancel_lifecycle(mock_popen, tmp_path):
    """
    Verify that the orchestrator does NOT run hooks if a task is cancelled.
    This also verifies that it skips the retry delay.
    """
    tasks_file = tmp_path / "tasks.yml"
    cli_runner = click.testing.CliRunner()

    # 1. Setup a roadmap with one task and one hook
    initial_data = tasks.Roadmap(
        context="test",
        tasks=[
            tasks.Task(
                id="task1",
                description="Task to be cancelled",
                status=tasks.TaskStatus.PENDING,
                attempts=0,
            )
        ],
        config=tasks.RoadmapConfig(hooks=["test_hook"], retries=2, runner="mock"),
    )
    tasks.save_tasks(tasks_file, initial_data)

    # 2. Mock the task runner to simulate cancellation
    mock_task_process = unittest.mock.MagicMock()
    mock_task_process.pid = 12345
    mock_task_process.returncode = -15  # SIGTERM
    mock_task_process.stdout = iter(["Task output\n"])

    def task_wait_side_effect():
        # Simulate external cancellation
        tasks.cancel_task(tasks_file, "task1")
        return -15

    mock_task_process.wait.side_effect = task_wait_side_effect
    mock_task_process.poll.return_value = -15

    # 3. Mock the hook runner (should NOT be called)
    mock_hook_process = unittest.mock.MagicMock()
    mock_hook_process.pid = 67890
    mock_hook_process.returncode = 0
    mock_hook_process.stdout = iter(["Hook output\n"])
    mock_hook_process.wait.return_value = 0
    mock_hook_process.poll.return_value = 0

    mock_popen.side_effect = [mock_task_process, mock_hook_process]

    def mocked_load_prompt(name, tasks_file=None):
        if name == "taskrunner":
            return "Task template {{description}}"
        return f"Hook template for {name}"

    with (
        unittest.mock.patch("lemming.runner.list_hooks", return_value=["test_hook"]),
        unittest.mock.patch(
            "lemming.runner.load_prompt", side_effect=mocked_load_prompt
        ),
    ):
        # 4. Run the loop
        # Use a long retry-delay to verify it's skipped
        start_time = time.time()
        result = cli_runner.invoke(
            main.cli,
            [
                "--verbose",
                "--tasks-file",
                str(tasks_file),
                "run",
                "--retry-delay",
                "10",
            ],
        )
        duration = time.time() - start_time

    # 5. Verify results
    # Attempt 1 was cancelled.
    assert "Task was cancelled. Skipping retry delay." in result.output
    # It should NOT show "Skipping final failure hooks" for Attempt 1 because it's not the final attempt yet.

    # Attempt 2 failed naturally (because we didn't mock a third call to Popen if it retries a second time)
    # Actually, my mock has 2 side effects: [mock_task_process, mock_hook_process]
    # Attempt 1 -> mock_task_process (-15)
    # Attempt 2 -> mock_hook_process (0)
    # Since Attempt 2 didn't call complete, it's a failure (final).
    # So it will run hooks.
    assert "Hook template for test_hook" in result.output

    assert duration < 5.0  # Should be much less than 10s (the retry delay)

    assert mock_popen.call_count == 3  # Attempt 1, Attempt 2, Hook (final failure)
