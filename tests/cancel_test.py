import subprocess
import time
import yaml
import click.testing

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

    with open(tasks_file, "r") as f:
        content = yaml.safe_load(f)

    content["tasks"][0]["status"] = "in_progress"
    content["tasks"][0]["pid"] = pid
    content["tasks"][0]["last_heartbeat"] = time.time()

    with open(tasks_file, "w") as f:
        yaml.dump(content, f)

    # 3. Cancel it
    result = runner.invoke(
        main.cli, ["--tasks-file", str(tasks_file), "cancel", task_id]
    )
    assert result.exit_code == 0
    assert f"Task {task_id} cancelled" in result.output

    # 4. Verify status is pending and PID is gone
    data = tasks.load_tasks(tasks_file)
    assert data.tasks[0].status == "pending"
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

    with open(tasks_file, "r") as f:
        content = yaml.safe_load(f)
    content["tasks"][0]["status"] = "in_progress"
    content["tasks"][0]["pid"] = task_pid
    content["tasks"][0]["last_heartbeat"] = time.time()
    with open(tasks_file, "w") as f:
        yaml.dump(content, f)

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
