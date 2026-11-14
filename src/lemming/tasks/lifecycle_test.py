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


def test_claim_task_starts_a_run(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(tasks=[models.Task(id="1", description="Task 1")]),
    )

    task = lifecycle.claim_task(tasks_file, "1", pid=123, working_dir=tmp_path)

    assert task is not None
    assert task.status == models.TaskStatus.RUNNING
    assert task.pid == 123
    assert task.attempts == 1
    assert task.pre_tree == snapshots.snapshot_tree(tasks_file, tmp_path)


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
    assert claimed.status == models.TaskStatus.RUNNING
    assert claimed.pid == 123
    assert claimed.attempts == 1


def test_claim_task_refuses_live_and_cut_off_runs(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.RUNNING,
                pid=os.getpid(),
                last_heartbeat=time.time(),
            )
        ]
    )
    persistence.save_tasks(tasks_file, data)
    assert lifecycle.claim_task(tasks_file, "1", pid=456) is None

    # A cut-off run must be settled by recovery, not taken over mid-attempt.
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        data.tasks[0].pid = 999999
        persistence.save_tasks(tasks_file, data)
    assert lifecycle.claim_task(tasks_file, "1", pid=789) is None


def test_claim_task_resumes_a_review_without_counting_it(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="1",
                    description="Reviewing",
                    status=models.TaskStatus.COMPLETING,
                    attempts=1,
                )
            ]
        ),
    )

    resumed = lifecycle.claim_task(tasks_file, "1", pid=123)

    assert resumed is not None
    assert resumed.status == models.TaskStatus.COMPLETING
    assert resumed.attempts == 1
    assert resumed.pid == 123


def test_update_heartbeat(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.RUNNING,
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
                status=models.TaskStatus.RUNNING,
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
                status=models.TaskStatus.RUNNING,
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
                    status=models.TaskStatus.RUNNING,
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
                status=models.TaskStatus.RUNNING,
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
                        status=models.TaskStatus.RUNNING,
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
        status=models.TaskStatus.RUNNING,
    )
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    # Without a requested completion there is nothing to reject.
    with pytest.raises(ValueError, match="not awaiting completion"):
        lifecycle.reject_task(tasks_file, "12345678", "suite fails")

    task.status = models.TaskStatus.COMPLETING
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    rejected = lifecycle.reject_task(tasks_file, "12345678", "suite fails")
    assert rejected.rejection == "suite fails"
    # The status is untouched so the remaining hooks still run.
    assert rejected.status == models.TaskStatus.COMPLETING


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

    # 2. RUNNING but no PID -> not active
    task_no_pid = models.Task(
        id="2",
        description="test",
        status=models.TaskStatus.RUNNING,
        last_heartbeat=now,
    )
    assert not lifecycle.is_task_active(task_no_pid, now)

    # 3. RUNNING, has PID, PID dead -> not active
    mock_is_pid_alive.return_value = False
    task_dead_pid = models.Task(
        id="3",
        description="test",
        status=models.TaskStatus.RUNNING,
        pid=123,
        last_heartbeat=now,
    )
    assert not lifecycle.is_task_active(task_dead_pid, now)

    # 4. RUNNING, has PID, PID alive, stale heartbeat -> not active
    mock_is_pid_alive.return_value = True
    stale_time = now - persistence.STALE_THRESHOLD - 10
    task_stale = models.Task(
        id="4",
        description="test",
        status=models.TaskStatus.RUNNING,
        pid=123,
        last_heartbeat=stale_time,
    )
    assert not lifecycle.is_task_active(task_stale, now)

    # 5. RUNNING, has PID, PID alive, fresh heartbeat -> active!
    task_active = models.Task(
        id="5",
        description="test",
        status=models.TaskStatus.RUNNING,
        pid=123,
        last_heartbeat=now,
    )
    assert lifecycle.is_task_active(task_active, now)

    # 6. Finalizing (requested_status), no PID -> not active (ready for hooks)
    task_finalizing = models.Task(
        id="6",
        description="test",
        status=models.TaskStatus.COMPLETING,
        last_heartbeat=now,
    )
    assert not lifecycle.is_task_active(task_finalizing, now)

    # 7. Finalizing (requested_status), PID alive, fresh heartbeat -> active!
    # (hooks running)
    task_hooks_running = models.Task(
        id="7",
        description="test",
        status=models.TaskStatus.COMPLETING,
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
        status=models.TaskStatus.RUNNING,
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


def test_cancel_running_task_by_prefix_restores_and_clears_trees(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "a.py").write_text("v1\n", encoding="utf-8")

    pre_tree = snapshots.snapshot_tree(tasks_file, work)
    assert pre_tree is not None

    (work / "a.py").write_text("half_done\n", encoding="utf-8")
    task = models.Task(
        id="cancel99",
        description="Running",
        status=models.TaskStatus.RUNNING,
        pre_tree=pre_tree,
        hook_trees={"testing": [pre_tree, pre_tree]},
    )
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    assert lifecycle.cancel_task(tasks_file, "cancel", working_dir=work)

    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"
    patch_file = (
        paths.get_artifacts_dir(tasks_file, "cancel99") / "cancelled.patch"
    )
    assert patch_file.is_file()
    assert "+half_done" in patch_file.read_text(encoding="utf-8")
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
        status=models.TaskStatus.RUNNING,
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
        status=models.TaskStatus.RUNNING,
        pre_tree="aaa",
    )
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))

    pruned: list[set[str]] = []
    monkeypatch.setattr(
        snapshots, "prune_snapshots", lambda _tf, keep: pruned.append(keep)
    )

    assert lifecycle.reset_task(tasks_file, "reset77")
    assert pruned == []
