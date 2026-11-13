import contextlib
import os
import signal
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from lemming import paths, snapshots

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
    assert finished is not None
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


def test_cancel_task_kills_detached_grandchild(tmp_path):
    """Verifies cancel kills a runner's child that left its process group."""
    pid_file = tmp_path / "grandchild.pid"
    child_code = (
        "import os, pathlib, subprocess, sys, time\n"
        "p = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(300)'],\n"
        "    start_new_session=True,\n"
        ")\n"
        f"pathlib.Path({str(pid_file)!r} + '.tmp').write_text(str(p.pid))\n"
        f"os.replace({str(pid_file)!r} + '.tmp', {str(pid_file)!r})\n"
        "time.sleep(300)\n"
    )
    runner_process = subprocess.Popen(
        [sys.executable, "-c", child_code], start_new_session=True
    )
    try:
        deadline = time.monotonic() + 10
        while not pid_file.exists():
            assert time.monotonic() < deadline, "Grandchild never started"
            time.sleep(0.1)
        grandchild = int(pid_file.read_text())

        tasks_file = tmp_path / "tasks.yml"
        persistence.save_tasks(
            tasks_file,
            models.Roadmap(
                tasks=[
                    models.Task(
                        id="1",
                        description="d",
                        status=models.TaskStatus.IN_PROGRESS,
                        pid=runner_process.pid,
                    )
                ],
            ),
        )
        assert lifecycle.cancel_task(tasks_file, "1")

        deadline = time.monotonic() + 10
        while persistence.is_pid_alive(grandchild):
            assert time.monotonic() < deadline, "Grandchild survived"
            time.sleep(0.1)
    finally:
        runner_process.kill()
        runner_process.wait()
        if pid_file.exists():
            with contextlib.suppress(OSError):
                os.kill(int(pid_file.read_text()), signal.SIGKILL)


def test_reject_task_blocks_only_a_requested_completion(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = models.Task(
        id="12345678",
        description="Task 1",
        status=models.TaskStatus.IN_PROGRESS,
    )
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    # Without a requested completion there is nothing to reject.
    with pytest.raises(ValueError, match="not awaiting completion"):
        lifecycle.reject_task(tasks_file, "12345678", "suite fails")

    task.requested_status = models.TaskStatus.COMPLETED
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    rejected = lifecycle.reject_task(tasks_file, "12345678", "suite fails")
    assert rejected.rejection == "suite fails"
    # The status is untouched so the remaining hooks still run.
    assert rejected.status == models.TaskStatus.IN_PROGRESS

    # A retry must not inherit the rejection.
    reverted = lifecycle.revert_task_to_pending(tasks_file, "12345678")
    assert reverted and reverted.rejection is None


def test_revert_task_to_pending_preserves_snapshot_fields(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = models.Task(
        id="12345678",
        description="Task 1",
        status=models.TaskStatus.IN_PROGRESS,
        pre_tree="aaa",
        runner_tree="bbb",
        post_tree="ccc",
        pre_head="ddd",
        diff_summary="1 file (+1/-0): a.py (+1/-0)",
    )
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    reverted = lifecycle.revert_task_to_pending(tasks_file, "12345678")
    assert reverted is not None
    assert reverted.pre_tree == "aaa"
    assert reverted.runner_tree == "bbb"
    assert reverted.post_tree == "ccc"
    assert reverted.pre_head == "ddd"
    assert reverted.diff_summary == "1 file (+1/-0): a.py (+1/-0)"


def test_reset_task(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    task_id = "12345678"
    snapshots_dir = snapshots.get_snapshots_dir(tasks_file)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    (snapshots_dir / "HEAD").write_text("ref: refs/heads/main\n")

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
                pre_tree="aaa",
                runner_tree="bbb",
                post_tree="ccc",
                pre_head="ddd",
                diff_summary="1 file (+1/-0): a.py (+1/-0)",
                hook_trees={"readability": ["bbb", "ccc"]},
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
    assert reset_task.pre_tree is None
    assert reset_task.runner_tree is None
    assert reset_task.post_tree is None
    assert reset_task.pre_head is None
    assert reset_task.diff_summary is None
    assert reset_task.hook_trees is None
    assert not log_file.exists()
    assert not snapshots_dir.exists()


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
    stale_time = now - persistence.STALE_THRESHOLD - 10
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


def test_record_resolved_command_persists_provenance(tmp_path):
    """The runner command of the current attempt survives the process."""
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(tasks=[models.Task(id="t1", description="d")]),
    )

    lifecycle.record_resolved_command(tasks_file, "t1", "agy --model x")

    data = persistence.load_tasks(tasks_file)
    assert data.tasks[0].resolved_command == "agy --model x"


def test_record_resolved_command_ignores_unknown_task(tmp_path):
    """Recording against a missing task is a no-op, not an error."""
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(tasks=[models.Task(id="t1", description="d")]),
    )

    lifecycle.record_resolved_command(tasks_file, "nope", "agy")

    data = persistence.load_tasks(tasks_file)
    assert data.tasks[0].resolved_command is None


def test_cancel_in_progress_task_saves_patch_and_restores_pre_tree(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "a.py").write_text("v1\n", encoding="utf-8")

    pre_tree = snapshots.snapshot_tree(tasks_file, work)
    assert pre_tree is not None

    task = models.Task(
        id="cancel12",
        description="In-progress task",
        status=models.TaskStatus.IN_PROGRESS,
        pre_tree=pre_tree,
    )
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    (work / "a.py").write_text("dirty\n", encoding="utf-8")
    (work / "stray.py").write_text("stray\n", encoding="utf-8")

    assert lifecycle.cancel_task(tasks_file, "cancel12", working_dir=work)

    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"
    assert not (work / "stray.py").exists()
    patch_file = (
        paths.get_artifacts_dir(tasks_file, "cancel12") / "cancelled.patch"
    )
    assert patch_file.is_file()
    assert "+dirty" in patch_file.read_text(encoding="utf-8")


def test_cancel_pending_rejected_task_by_prefix_restores_and_clears_trees(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "a.py").write_text("v1\n", encoding="utf-8")

    pre_tree = snapshots.snapshot_tree(tasks_file, work)
    assert pre_tree is not None

    (work / "a.py").write_text("rejected_v2\n", encoding="utf-8")
    runner_tree = snapshots.snapshot_tree(tasks_file, work)
    assert runner_tree is not None

    task = models.Task(
        id="cancel99",
        description="Pending after hook rejection",
        status=models.TaskStatus.PENDING,
        pre_tree=pre_tree,
        runner_tree=runner_tree,
        hook_trees={"testing": [runner_tree, runner_tree]},
    )
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    assert lifecycle.cancel_task(tasks_file, "cancel", working_dir=work)

    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"
    patch_file = (
        paths.get_artifacts_dir(tasks_file, "cancel99") / "cancelled.patch"
    )
    assert patch_file.is_file()
    assert "+rejected_v2" in patch_file.read_text(encoding="utf-8")
    updated = persistence.load_tasks(tasks_file).tasks[0]
    assert updated.status == models.TaskStatus.CANCELLED
    assert updated.pre_tree is None
    assert updated.runner_tree is None
    assert updated.hook_trees is None


def test_cancel_task_skips_restore_if_orchestrator_restored_during_kill(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "a.py").write_text("v1\n", encoding="utf-8")
    pre_tree = snapshots.snapshot_tree(tasks_file, work)
    assert pre_tree is not None

    (work / "a.py").write_text("dirty\n", encoding="utf-8")
    task = models.Task(
        id="cancel88",
        description="Race task",
        status=models.TaskStatus.IN_PROGRESS,
        pid=99999,
        pre_tree=pre_tree,
    )
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    def fake_kill(_pid):
        mid = persistence.load_tasks(tasks_file).tasks[0]
        assert mid.status == models.TaskStatus.CANCELLED
        assert mid.pre_tree == pre_tree
        # Simulate _handle_runner_exit restoring and clearing pre_tree first.
        with persistence.lock_tasks(tasks_file):
            data = persistence.load_tasks(tasks_file)
            data.tasks[0].pre_tree = None
            persistence.save_tasks(tasks_file, data)
        (work / "a.py").write_text("user_post_restore_edit\n", encoding="utf-8")

    monkeypatch.setattr(lifecycle, "_kill_pid_tree", fake_kill)

    assert lifecycle.cancel_task(tasks_file, "cancel88", working_dir=work)
    assert (work / "a.py").read_text(
        encoding="utf-8"
    ) == "user_post_restore_edit\n"


def test_reset_task_skips_prune_when_task_was_in_progress(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    task = models.Task(
        id="reset77",
        description="In progress hook reset",
        status=models.TaskStatus.IN_PROGRESS,
        pre_tree="aaa",
    )
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    pruned: list[set[str]] = []
    monkeypatch.setattr(
        snapshots, "prune_snapshots", lambda _tf, keep: pruned.append(keep)
    )

    assert lifecycle.reset_task(tasks_file, "reset77")
    assert pruned == []


def test_write_patch_artifact_keeps_non_utf8_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    pre_tree = snapshots.snapshot_tree(tasks_file, work)
    notes = work / "notes.txt"
    notes.write_bytes(b"caf\xe9\n")
    post_tree = snapshots.snapshot_tree(tasks_file, work)

    lifecycle.write_patch_artifact(
        tasks_file, "latin1", "attempt-1.patch", pre_tree, post_tree
    )

    notes.unlink()
    patch_file = (
        paths.get_artifacts_dir(tasks_file, "latin1") / "attempt-1.patch"
    )
    subprocess.run(["git", "apply", str(patch_file)], cwd=work, check=True)
    assert notes.read_bytes() == b"caf\xe9\n"
