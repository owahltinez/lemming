import os
import time
from unittest.mock import patch

from lemming import paths
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
        ],
    )
    persistence.save_tasks(tasks_file, data)

    with patch("lemming.tasks.lifecycle.os.killpg"):
        success = lifecycle.cancel_task(tasks_file, "1")
        assert success is True

    updated_data = persistence.load_tasks(tasks_file)
    assert updated_data.tasks[0].status == models.TaskStatus.CANCELLED
    assert updated_data.tasks[0].pid is None


def test_cancel_task_kills_loop_pid(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.IN_PROGRESS,
                pid=123,
            )
        ],
    )
    persistence.save_tasks(tasks_file, data)

    # Mock get_loop_pid to return a dummy PID
    import signal

    with (
        patch("lemming.persistence.get_loop_pid", return_value=456),
        patch("lemming.tasks.lifecycle.os.getpgid", return_value=123),
        patch("lemming.tasks.lifecycle.os.kill") as mock_kill,
        patch("lemming.tasks.lifecycle.os.killpg") as mock_killpg,
    ):
        success = lifecycle.cancel_task(tasks_file, "1")
        assert success is True

        # Verify task PID was killed
        mock_killpg.assert_called_once_with(123, signal.SIGTERM)
        # Verify loop PID was killed
        mock_kill.assert_any_call(456, signal.SIGTERM)

        # Verify task is marked as cancelled
        updated_data = persistence.load_tasks(tasks_file)
        assert updated_data.tasks[0].status == models.TaskStatus.CANCELLED


def test_reset_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task_id = "12345678"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id=task_id,
                description="Task 1",
                status=models.TaskStatus.COMPLETED,
                progress=["done"],
            )
        ],
    )
    persistence.save_tasks(tasks_file, data)

    # Create log file
    log_file = paths.get_log_file(tasks_file, task_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("log content")

    reset_task = lifecycle.reset_task(tasks_file, task_id)
    assert reset_task.status == models.TaskStatus.PENDING
    assert reset_task.progress == []
    assert not log_file.exists()


@patch("lemming.tasks.lifecycle.is_pid_alive")
def test_is_task_active(mock_is_pid_alive):
    now = time.time()

    # 1. Pending task is never active
    task_pending = models.Task(
        id="1", description="test", status=models.TaskStatus.PENDING
    )
    assert not lifecycle.is_task_active(task_pending, now)

    # 2. IN_PROGRESS but no PID -> not active
    task_no_pid = models.Task(
        id="2",
        description="test",
        status=models.TaskStatus.IN_PROGRESS,
        last_heartbeat=now,
    )
    assert not lifecycle.is_task_active(task_no_pid, now)

    # 3. IN_PROGRESS, has PID, PID dead -> not active
    mock_is_pid_alive.return_value = False
    task_dead_pid = models.Task(
        id="3",
        description="test",
        status=models.TaskStatus.IN_PROGRESS,
        pid=123,
        last_heartbeat=now,
    )
    assert not lifecycle.is_task_active(task_dead_pid, now)

    # 4. IN_PROGRESS, has PID, PID alive, stale heartbeat -> not active
    mock_is_pid_alive.return_value = True
    stale_time = now - lifecycle.STALE_THRESHOLD - 10
    task_stale = models.Task(
        id="4",
        description="test",
        status=models.TaskStatus.IN_PROGRESS,
        pid=123,
        last_heartbeat=stale_time,
    )
    assert not lifecycle.is_task_active(task_stale, now)

    # 5. IN_PROGRESS, has PID, PID alive, fresh heartbeat -> active!
    task_active = models.Task(
        id="5",
        description="test",
        status=models.TaskStatus.IN_PROGRESS,
        pid=123,
        last_heartbeat=now,
    )
    assert lifecycle.is_task_active(task_active, now)

    # 6. Finalizing (requested_status), no PID -> not active (ready for hooks)
    task_finalizing = models.Task(
        id="6",
        description="test",
        status=models.TaskStatus.IN_PROGRESS,
        requested_status=models.TaskStatus.COMPLETED,
        last_heartbeat=now,
    )
    assert not lifecycle.is_task_active(task_finalizing, now)

    # 7. Finalizing (requested_status), PID alive, fresh heartbeat -> active! (hooks running)
    task_hooks_running = models.Task(
        id="7",
        description="test",
        status=models.TaskStatus.IN_PROGRESS,
        requested_status=models.TaskStatus.COMPLETED,
        pid=123,
        last_heartbeat=now,
    )
    assert lifecycle.is_task_active(task_hooks_running, now)
