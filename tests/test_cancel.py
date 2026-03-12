import time
import subprocess
from lemming.core import save_tasks, load_tasks, cancel_task


def test_cancel_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"

    # Start a dummy process that sleeps in a new session
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    pid = proc.pid

    data = {
        "context": "test",
        "tasks": [
            {
                "id": "task1",
                "description": "task 1",
                "status": "in_progress",
                "pid": pid,
                "last_heartbeat": time.time(),
            }
        ],
    }
    save_tasks(tasks_file, data)

    # Verify process is running
    assert proc.poll() is None

    # Cancel the task
    assert cancel_task(tasks_file, "task1") is True

    # Give it a moment to die
    time.sleep(0.1)

    # Verify process is killed
    assert proc.poll() is not None

    # Verify task status is pending and PID is removed
    updated_data = load_tasks(tasks_file)
    task = updated_data["tasks"][0]
    assert task["status"] == "pending"
    assert "pid" not in task
    assert "last_heartbeat" not in task


def test_cancel_task_no_pid(tmp_path):
    tasks_file = tmp_path / "tasks.yml"

    data = {
        "context": "test",
        "tasks": [
            {
                "id": "task2",
                "description": "task 2",
                "status": "in_progress",
                "last_heartbeat": time.time(),
            }
        ],
    }
    save_tasks(tasks_file, data)

    assert cancel_task(tasks_file, "task2") is True

    updated_data = load_tasks(tasks_file)
    task = updated_data["tasks"][0]
    assert task["status"] == "pending"
    assert "last_heartbeat" not in task
