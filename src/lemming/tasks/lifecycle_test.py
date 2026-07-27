import os
import signal
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
    tasks_file = tmp_path / "tasks.yml"
    persistence.acquire_loop_lock(tasks_file)
    assert lifecycle.is_loop_running(tasks_file) is True

    # Manually overwrite with stale PID
    lock_path = (
        paths.get_project_dir(tasks_file) / persistence.LOOP_LOCK_FILENAME
    )
    lock_path.write_text("999999")
    assert lifecycle.is_loop_running(tasks_file) is False
    assert not lock_path.exists()


def test_update_run_time():
    task = models.Task(
        id="1", description="test", last_started_at=time.time() - 5
    )
    lifecycle.update_run_time(task)
    assert task.run_time >= 5.0


def test_record_execution_time_accumulates_by_component(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(tasks=[models.Task(id="1", description="test")]),
    )

    lifecycle.record_execution_time(tasks_file, "1", "runner", 12.5)
    lifecycle.record_execution_time(tasks_file, "1", "runner", 2.5)
    lifecycle.record_execution_time(tasks_file, "1", "hook:readability", 4.0)

    task = persistence.load_tasks(tasks_file).tasks[0]
    assert task.execution_times == {
        "runner": 15.0,
        "hook:readability": 4.0,
    }


def test_active_execution_is_recorded_then_cleared(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(tasks=[models.Task(id="1", description="test")]),
    )

    lifecycle.mark_execution_started(
        tasks_file, "1", "hook:testing", started_at=123.0
    )
    active_task = persistence.load_tasks(tasks_file).tasks[0]
    assert active_task.active_execution_component == "hook:testing"
    assert active_task.active_execution_started_at == 123.0

    lifecycle.record_execution_time(tasks_file, "1", "hook:testing", 5.0)
    finished_task = persistence.load_tasks(tasks_file).tasks[0]
    assert finished_task.execution_times == {"hook:testing": 5.0}
    assert finished_task.active_execution_component is None
    assert finished_task.active_execution_started_at is None


def test_mark_task_in_progress(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1", description="Task 1", status=models.TaskStatus.PENDING
            )
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
            models.Task(
                id="1", description="Task 1", status=models.TaskStatus.PENDING
            )
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
        data.tasks[0].last_heartbeat = time.time() - (
            persistence.STALE_THRESHOLD + 1
        )
        persistence.save_tasks(tasks_file, data)

    claimed_stale = lifecycle.claim_task(tasks_file, "1", pid=789)
    assert claimed_stale is not None
    assert claimed_stale.pid == 789
    assert (
        claimed_stale.attempts == 1
    )  # 0 + 1 because we manually created it with 0


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
                id="1",
                description="Task 1",
                status=models.TaskStatus.IN_PROGRESS,
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

    with (
        patch("lemming.tasks.lifecycle.os.killpg"),
        patch("lemming.tasks.lifecycle.is_pid_alive", return_value=False),
    ):
        success = lifecycle.cancel_task(tasks_file, "1")
        assert success is True

    updated_data = persistence.load_tasks(tasks_file)
    assert updated_data.tasks[0].status == models.TaskStatus.CANCELLED
    assert updated_data.tasks[0].pid is None


def test_cancel_active_task_kills_runner_but_not_loop(tmp_path):
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

    with (
        patch("lemming.persistence.get_loop_pid", return_value=456),
        patch("lemming.tasks.lifecycle.os.kill") as mock_kill,
        patch("lemming.tasks.lifecycle._kill_pid_tree") as mock_kill_tree,
    ):
        success = lifecycle.cancel_task(tasks_file, "1")

    assert success is True
    mock_kill_tree.assert_called_once_with(123)
    mock_kill.assert_not_called()

    updated_data = persistence.load_tasks(tasks_file)
    assert updated_data.tasks[0].status == models.TaskStatus.CANCELLED


def test_cancel_pending_task_does_not_kill_process(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="1",
                    description="Pending task",
                    status=models.TaskStatus.PENDING,
                )
            ]
        ),
    )

    with patch("lemming.tasks.lifecycle._kill_pid_tree") as mock_kill_tree:
        success = lifecycle.cancel_task(tasks_file, "1")

    assert success is True
    mock_kill_tree.assert_not_called()
    task = persistence.load_tasks(tasks_file).tasks[0]
    assert task.status == models.TaskStatus.CANCELLED


def test_cancel_during_claim_does_not_kill_loop_pid(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="1",
                    description="Claimed task",
                    status=models.TaskStatus.IN_PROGRESS,
                    pid=456,
                )
            ]
        ),
    )

    with (
        patch("lemming.persistence.get_loop_pid", return_value=456),
        patch("lemming.tasks.lifecycle._kill_pid_tree") as mock_kill_tree,
    ):
        success = lifecycle.cancel_task(tasks_file, "1")

    assert success is True
    mock_kill_tree.assert_not_called()


def test_kill_pid_tree_escalates_to_sigkill():
    """Verifies SIGKILL escalation when SIGTERM does not stop the process."""
    with (
        patch("lemming.tasks.lifecycle.os.getpgid", return_value=54321),
        patch("lemming.tasks.lifecycle.os.killpg") as mock_killpg,
        patch("lemming.tasks.lifecycle.is_pid_alive", return_value=True),
        patch.object(lifecycle, "KILL_GRACE_SECONDS", 0),
    ):
        lifecycle._kill_pid_tree(12345)

    assert mock_killpg.call_args_list == [
        ((54321, signal.SIGTERM),),
        ((54321, signal.SIGKILL),),
    ]


def test_kill_pid_tree_no_escalation_when_process_exits():
    """Verifies no SIGKILL is sent when the process exits on SIGTERM."""
    with (
        patch("lemming.tasks.lifecycle.os.getpgid", return_value=54321),
        patch("lemming.tasks.lifecycle.os.killpg") as mock_killpg,
        patch("lemming.tasks.lifecycle.is_pid_alive", return_value=False),
    ):
        lifecycle._kill_pid_tree(12345)

    mock_killpg.assert_called_once_with(54321, signal.SIGTERM)


def test_cancel_task_escalates_sigterm_immune_process(tmp_path):
    """Verifies cancellation kills a process that ignores SIGTERM.

    Also verifies the task state is saved as CANCELLED before any signal
    is sent, since the kill (which can wait out a grace period) must
    happen outside the tasks-file lock.
    """
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.IN_PROGRESS,
                pid=12345,
            )
        ],
    )
    persistence.save_tasks(tasks_file, data)

    status_at_first_signal = []

    def record_status(*args):
        saved = persistence.load_tasks(tasks_file)
        status_at_first_signal.append(saved.tasks[0].status)

    with (
        patch("lemming.tasks.lifecycle.os.getpgid", return_value=54321),
        patch(
            "lemming.tasks.lifecycle.os.killpg", side_effect=record_status
        ) as mock_killpg,
        patch("lemming.tasks.lifecycle.is_pid_alive", return_value=True),
        patch.object(lifecycle, "KILL_GRACE_SECONDS", 0),
    ):
        success = lifecycle.cancel_task(tasks_file, "1")

    assert success is True
    assert mock_killpg.call_args_list == [
        ((54321, signal.SIGTERM),),
        ((54321, signal.SIGKILL),),
    ]
    assert status_at_first_signal[0] == models.TaskStatus.CANCELLED


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
                execution_times={"runner": 10.0},
                superseded_at=100.0,
                superseded_reason="split",
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
    assert reset_task.execution_times is None
    assert reset_task.superseded_at is None
    assert reset_task.superseded_reason is None
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

    # 7. Finalizing (requested_status), PID alive, fresh heartbeat -> active!
    # (hooks running)
    task_hooks_running = models.Task(
        id="7",
        description="test",
        status=models.TaskStatus.IN_PROGRESS,
        requested_status=models.TaskStatus.COMPLETED,
        pid=123,
        last_heartbeat=now,
    )
    assert lifecycle.is_task_active(task_hooks_running, now)
