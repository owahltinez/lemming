import os
import time
from unittest.mock import patch

from .. import models, persistence
from . import lifecycle


def test_generate_task_id():
    id1 = lifecycle.generate_task_id()
    id2 = lifecycle.generate_task_id()
    assert len(id1) == 8
    assert id1 != id2


def test_is_pid_alive():
    assert lifecycle.is_pid_alive(os.getpid()) is True
    assert lifecycle.is_pid_alive(999999) is False


def test_is_loop_running_stale_pid(tmp_path):
    from lemming import paths

    tasks_file = tmp_path / "tasks.yml"
    persistence.acquire_loop_lock(tasks_file)
    assert lifecycle.is_loop_running(tasks_file) is True

    # Manually overwrite with stale PID
    lock_path = paths.get_project_dir(tasks_file) / persistence.LOOP_LOCK_FILENAME
    lock_path.write_text("999999")
    assert lifecycle.is_loop_running(tasks_file) is False


def test_update_run_time():
    task = models.Task(id="1", description="test", last_started_at=time.time() - 5)
    lifecycle.update_run_time(task)
    assert task.run_time >= 5.0


def test_mark_task_in_progress(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(id="1", description="Task 1", status=models.TaskStatus.PENDING)
        ]
    )
    persistence.save_tasks(tasks_file, data)

    success = lifecycle.mark_task_in_progress(tasks_file, "1", pid=123)
    assert success is True

    updated_data = persistence.load_tasks(tasks_file)
    assert updated_data.tasks[0].status == models.TaskStatus.IN_PROGRESS
    assert updated_data.tasks[0].pid == 123


def test_claim_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(id="1", description="Task 1", status=models.TaskStatus.PENDING)
        ]
    )
    persistence.save_tasks(tasks_file, data)

    claimed = lifecycle.claim_task(tasks_file, "1", pid=123)
    assert claimed is not None
    assert claimed.status == models.TaskStatus.IN_PROGRESS
    assert claimed.pid == 123
    assert claimed.attempts == 1


def test_claim_already_in_progress(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.IN_PROGRESS,
                pid=os.getpid(),
                last_heartbeat=time.time(),
            )
        ]
    )
    persistence.save_tasks(tasks_file, data)

    # Second claim should fail
    claimed_again = lifecycle.claim_task(tasks_file, "1", pid=456)
    assert claimed_again is None

    # But if it's stale, it should succeed
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        data.tasks[0].last_heartbeat = time.time() - (persistence.STALE_THRESHOLD + 1)
        persistence.save_tasks(tasks_file, data)

    claimed_stale = lifecycle.claim_task(tasks_file, "1", pid=789)
    assert claimed_stale is not None
    assert claimed_stale.pid == 789
    assert claimed_stale.attempts == 1  # 0 + 1 because we manually created it with 0


def test_finish_task_attempt(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.IN_PROGRESS,
                last_started_at=time.time(),
            )
        ]
    )
    persistence.save_tasks(tasks_file, data)

    finished = lifecycle.finish_task_attempt(tasks_file, "1")
    assert finished.status == models.TaskStatus.PENDING
    assert finished.pid is None


def test_update_heartbeat(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1", description="Task 1", status=models.TaskStatus.IN_PROGRESS
            )
        ]
    )
    persistence.save_tasks(tasks_file, data)

    success = lifecycle.update_heartbeat(tasks_file, "1", pid=123)
    assert success is True

    updated_data = persistence.load_tasks(tasks_file)
    assert updated_data.tasks[0].last_heartbeat is not None
    assert updated_data.tasks[0].pid == 123


def test_cancel_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.IN_PROGRESS,
                pid=os.getpid(),
            )
        ]
    )
    persistence.save_tasks(tasks_file, data)

    with patch("os.killpg"):
        success = lifecycle.cancel_task(tasks_file, "1")
        assert success is True

    updated_data = persistence.load_tasks(tasks_file)
    assert updated_data.tasks[0].status == models.TaskStatus.PENDING
    assert updated_data.tasks[0].pid is None


def test_reset_task(tmp_path):
    from lemming import paths

    tasks_file = tmp_path / "tasks.yml"
    task_id = "12345678"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id=task_id,
                description="Task 1",
                status=models.TaskStatus.COMPLETED,
                outcomes=["done"],
            )
        ]
    )
    persistence.save_tasks(tasks_file, data)

    # Create log file
    log_file = paths.get_log_file(tasks_file, task_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("log content")

    reset_task = lifecycle.reset_task(tasks_file, task_id)
    assert reset_task.status == models.TaskStatus.PENDING
    assert reset_task.outcomes == []
    assert not log_file.exists()
