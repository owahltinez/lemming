import os
import subprocess
import time
from unittest import mock

import pytest

from lemming import models, paths, persistence, prompts, snapshots
from lemming.orchestrator import (
    HOOK_TIME_LIMIT,
    _handle_runner_exit,
    _process_exhausted_retries,
    _process_finalizing_task,
    format_duration,
    parse_timeout,
    run_hooks,
    run_loop,
)
from lemming.runner import RETURNCODE_TIMEOUT
from lemming.tasks import lifecycle, operations, progress


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    monkeypatch.setattr(snapshots, "snapshot_tree", lambda *_a, **_kw: None)
    monkeypatch.setattr(snapshots, "head_commit", lambda *_a, **_kw: None)
    test_tasks_file = tmp_path / "tasks_test.yml"

    # Scaffold a valid file with one task
    initial_data = models.Roadmap(
        goal="Initial goal",
        tasks=[
            models.Task(
                id="task1",
                description="Task 1",
                status=models.TaskStatus.PENDING,
                attempts=0,
                progress=[],
            )
        ],
        config=models.RoadmapConfig(retries=3, runner="agy"),
    )
    persistence.save_tasks(test_tasks_file, initial_data)
    return test_tasks_file, initial_data


def test_parse_timeout():
    assert parse_timeout("0") == 0.0
    assert parse_timeout("-1h") == 0.0
    assert parse_timeout("8h") == 8 * 3600.0
    assert parse_timeout("30m") == 30 * 60.0
    assert parse_timeout("90s") == 90.0
    assert parse_timeout("invalid") == 0.0


def test_format_duration():
    assert format_duration(0) == "none"
    assert format_duration(-1) == "none"
    assert format_duration(30) == "30m"
    assert format_duration(60) == "1h"
    assert format_duration(90) == "90m"
    assert format_duration(120) == "2h"


@mock.patch("lemming.snapshots.head_commit", return_value=None)
@mock.patch("lemming.snapshots.snapshot_tree", return_value=None)
@mock.patch("lemming.runner.subprocess.Popen")
def test_run_loop_success(mock_popen, _mock_snap, _mock_head, setup_env):
    test_tasks_file, initial_data = setup_env
    # Simulate runner reporting success
    mock_process = mock.MagicMock()
    mock_process.pid = 12345
    mock_process.poll.side_effect = [None, 0]
    mock_process.returncode = 0
    mock_process.stdout = iter(["stdout\n"])
    mock_process.communicate.return_value = ("stdout", "stderr")

    def wait_side_effect():
        with persistence.lock_tasks(test_tasks_file):
            data = persistence.load_tasks(test_tasks_file)
            data.tasks[0].status = models.TaskStatus.COMPLETED
            data.tasks[0].completed_at = time.time()
            persistence.save_tasks(test_tasks_file, data)
        return 0

    mock_process.wait.side_effect = wait_side_effect
    mock_popen.return_value = mock_process

    run_loop(
        test_tasks_file,
        verbose=True,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    data = persistence.load_tasks(test_tasks_file)
    assert data.tasks[0].status == models.TaskStatus.COMPLETED


@mock.patch("lemming.snapshots.head_commit", return_value=None)
@mock.patch("lemming.snapshots.snapshot_tree", return_value=None)
@mock.patch("lemming.runner.subprocess.Popen")
@mock.patch("time.sleep", return_value=None)
def test_run_loop_retry_and_fail(
    mock_sleep, mock_popen, _mock_snap, _mock_head, setup_env
):
    test_tasks_file, initial_data = setup_env
    # Runner finishes but doesn't report completion
    mock_process = mock.MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = 0
    mock_process.returncode = 0
    mock_process.stdout = iter(["stdout\n"])
    mock_process.communicate.return_value = ("stdout", "stderr")
    mock_popen.return_value = mock_process

    # Configure 2 retries
    data = persistence.load_tasks(test_tasks_file)
    data.config.retries = 2
    persistence.save_tasks(test_tasks_file, data)

    run_loop(
        test_tasks_file,
        verbose=True,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    data = persistence.load_tasks(test_tasks_file)
    assert data.tasks[0].attempts == 2
    assert data.tasks[0].status == models.TaskStatus.FAILED


def test_synchronous_hooks_execution_timing(setup_env):
    """Verifies a new task starts only AFTER previous hooks finished."""
    test_tasks_file, initial_data = setup_env
    # 1. Setup two pending tasks.
    now = time.time()
    with persistence.lock_tasks(test_tasks_file):
        data = persistence.load_tasks(test_tasks_file)
        data.tasks = [
            models.Task(
                id="task2",
                description="Task 2",
                status=models.TaskStatus.PENDING,
                created_at=now,
            ),
            models.Task(
                id="task1",
                description="Task 1",
                status=models.TaskStatus.PENDING,
                created_at=now + 10,
            ),
        ]
        data.config.retries = 1
        data.config.runner = "true"
        persistence.save_tasks(test_tasks_file, data)

    task_starts = {}
    hook_ends = {}

    def mocked_run_with_heartbeat(
        cmd, t_file, t_id, verbose, echo_fn, header=None, cwd=None, time_limit=0
    ):
        if header and header.startswith("Hook:"):
            time.sleep(0.1)
            hook_ends[t_id] = time.time()
            return 0, "hook stdout", ""
        else:
            task_starts[t_id] = time.time()
            return 0, "task stdout", ""

    def mocked_finish_task_attempt(t_file, t_id):
        with persistence.lock_tasks(t_file):
            data = persistence.load_tasks(t_file)
            task = next(t for t in data.tasks if t.id == t_id)
            task.requested_status = models.TaskStatus.COMPLETED
            task.status = models.TaskStatus.IN_PROGRESS
            persistence.save_tasks(t_file, data)
            return task

    with (
        mock.patch(
            "lemming.runner.run_with_heartbeat",
            side_effect=mocked_run_with_heartbeat,
        ),
        mock.patch(
            "lemming.tasks.lifecycle.finish_task_attempt",
            side_effect=mocked_finish_task_attempt,
        ),
        mock.patch(
            "lemming.orchestrator.select_hooks", return_value=["test_hook"]
        ),
        mock.patch(
            "lemming.prompts.prepare_hook_prompt", return_value="Dummy hook"
        ),
    ):
        run_loop(
            test_tasks_file,
            verbose=True,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
        )

    assert "task1" in task_starts
    assert "task2" in task_starts
    assert "task2" in hook_ends

    assert task_starts["task1"] >= hook_ends["task2"], (
        "Task 1 should start after Task 2 hooks finish"
    )


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_loop_calls_runner_with_header(mock_run, setup_env):
    test_tasks_file, initial_data = setup_env
    working_dir = test_tasks_file.parent / "project with spaces"
    # Setup runner mock
    mock_run.return_value = (0, "output", "")

    # Mock finish_task_attempt to return a completed task to end loop
    mock_task = initial_data.tasks[0]
    mock_task.status = models.TaskStatus.COMPLETED
    with mock.patch(
        "lemming.tasks.lifecycle.finish_task_attempt", return_value=mock_task
    ):
        run_loop(
            test_tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
            working_dir=working_dir,
        )

    # Verify run_with_heartbeat was called with header="Task Runner"
    args, kwargs = mock_run.call_args
    assert kwargs.get("header") == "Task Runner"
    cmd = args[0]
    assert cmd[cmd.index("--add-dir") + 1] == str(working_dir)


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("time.sleep", return_value=None)
def test_run_loop_treats_a_runner_that_never_started_as_a_failure(
    mock_sleep, mock_run, setup_env
):
    # A spawn failure used to leave the return code at its initialised zero,
    # so the loop finalized the task as though the agent had run and exited
    # cleanly. Unattended, that marches through a roadmap reporting success
    # for work nothing ever did.
    test_tasks_file, _ = setup_env
    mock_run.side_effect = FileNotFoundError("no such runner")

    run_loop(
        test_tasks_file,
        verbose=True,
        retry_delay=1,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    # The runner never ran, so the attempt must not be charged to the task.
    # With the bug the task burned all three retries and was marked failed.
    data = persistence.load_tasks(test_tasks_file)
    assert data.tasks[0].attempts == 0
    assert data.tasks[0].status == models.TaskStatus.PENDING


@mock.patch("time.sleep", return_value=None)
def test_run_loop_hints_when_runner_is_not_found(mock_sleep, setup_env, capsys):
    test_tasks_file, _ = setup_env
    data = persistence.load_tasks(test_tasks_file)
    data.config.runner = "lemming-no-such-runner"
    persistence.save_tasks(test_tasks_file, data)

    run_loop(
        test_tasks_file,
        verbose=False,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    assert "Command 'lemming-no-such-runner' not found" in (
        capsys.readouterr().out
    )


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("time.sleep", return_value=None)
def test_run_loop_cancelled(mock_sleep, mock_run, setup_env):
    test_tasks_file, initial_data = setup_env
    # Simulate a task being cancelled (exit code -15)
    mock_run.return_value = (-15, "cancelled", "error")

    # Configure retries to ensure it DOES NOT retry
    data = persistence.load_tasks(test_tasks_file)
    data.config.retries = 3
    persistence.save_tasks(test_tasks_file, data)

    run_loop(
        test_tasks_file,
        verbose=True,
        retry_delay=1,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    # It should only have 1 attempt
    data = persistence.load_tasks(test_tasks_file)
    assert data.tasks[0].attempts == 1

    # Verify that sleep was NOT called with the retry_delay (1)
    # It might be called with other values if I didn't mock enough,
    # but here it should not be called at all after the break.
    for call in mock_sleep.call_args_list:
        assert call[0][0] != 1, (
            "Should not sleep with retry_delay on cancellation"
        )


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("time.sleep", return_value=None)
def test_run_loop_timeout_retries(mock_sleep, mock_run, setup_env):
    """Verifies that a timed-out task is retried without running hooks."""
    test_tasks_file, initial_data = setup_env
    # First call: timeout. Second call: timeout again. Exhausts retries.
    mock_run.return_value = (RETURNCODE_TIMEOUT, "output", "")

    data = persistence.load_tasks(test_tasks_file)
    data.config.retries = 2
    data.config.time_limit = 60
    persistence.save_tasks(test_tasks_file, data)

    run_loop(
        test_tasks_file,
        verbose=False,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    data = persistence.load_tasks(test_tasks_file)
    assert data.tasks[0].attempts == 2
    assert data.tasks[0].status == models.TaskStatus.FAILED


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("time.sleep", return_value=None)
def test_run_loop_passes_time_limit(mock_sleep, mock_run, setup_env):
    """Verifies that time_limit from config is passed to the runner."""
    test_tasks_file, initial_data = setup_env
    mock_run.return_value = (0, "output", "")

    data = persistence.load_tasks(test_tasks_file)
    data.config.time_limit = 30
    persistence.save_tasks(test_tasks_file, data)

    mock_task = initial_data.tasks[0]
    mock_task.status = models.TaskStatus.COMPLETED
    with mock.patch(
        "lemming.tasks.lifecycle.finish_task_attempt", return_value=mock_task
    ):
        run_loop(
            test_tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
        )

    _, kwargs = mock_run.call_args
    assert kwargs.get("time_limit") == 30


@mock.patch("lemming.orchestrator.run_hooks")
def test_process_exhausted_retries_aborts(mock_run_hooks, setup_env):
    test_tasks_file, initial_data = setup_env
    # 1. Setup task that has exhausted retries and won't be healed
    data = persistence.load_tasks(test_tasks_file)
    task = data.tasks[0]
    task.attempts = 3
    persistence.save_tasks(test_tasks_file, data)

    should_abort = _process_exhausted_retries(
        test_tasks_file,
        task.id,
        retries=3,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=["roadmap"],
        working_dir=None,
        time_limit=1,
    )
    assert should_abort
    mock_run_hooks.assert_called_once()
    assert (
        mock_run_hooks.call_args[1]["final_status"] == models.TaskStatus.FAILED
    )


@mock.patch("lemming.orchestrator.run_hooks")
def test_process_exhausted_retries_healed(mock_run_hooks, setup_env):
    test_tasks_file, initial_data = setup_env

    # Setup task but simulate a hook healing it by resetting attempts
    def fake_run_hooks(*args, **kwargs):
        data = persistence.load_tasks(test_tasks_file)
        data.tasks[0].attempts = 0  # Healed!
        persistence.save_tasks(test_tasks_file, data)

    mock_run_hooks.side_effect = fake_run_hooks

    data = persistence.load_tasks(test_tasks_file)
    task = data.tasks[0]
    task.attempts = 3
    persistence.save_tasks(test_tasks_file, data)

    should_abort = _process_exhausted_retries(
        test_tasks_file,
        task.id,
        retries=3,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=["roadmap"],
        working_dir=None,
        time_limit=1,
    )
    assert not should_abort
    mock_run_hooks.assert_called_once()


@mock.patch("lemming.orchestrator.run_hooks")
def test_process_exhausted_retries_continues_when_superseded(
    mock_run_hooks, setup_env
):
    test_tasks_file, _ = setup_env
    task_id = persistence.load_tasks(test_tasks_file).tasks[0].id

    def fake_run_hooks(*args, **kwargs):
        operations.supersede_task(
            test_tasks_file,
            task_id,
            "split after reaching the time limit",
        )

    mock_run_hooks.side_effect = fake_run_hooks
    data = persistence.load_tasks(test_tasks_file)
    data.tasks[0].attempts = 3
    persistence.save_tasks(test_tasks_file, data)

    should_abort = _process_exhausted_retries(
        test_tasks_file,
        task_id,
        retries=3,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=["roadmap"],
        working_dir=None,
        time_limit=1,
    )

    assert not should_abort
    task = persistence.load_tasks(test_tasks_file).tasks[0]
    assert task.status == models.TaskStatus.SUPERSEDED


@mock.patch("lemming.orchestrator.run_hooks")
def test_process_finalizing_task(mock_run_hooks, setup_env):
    test_tasks_file, initial_data = setup_env
    _process_finalizing_task(
        test_tasks_file,
        "task1",
        requested_status=models.TaskStatus.COMPLETED,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=["readability"],
        working_dir=None,
        time_limit=1,
    )
    mock_run_hooks.assert_called_once()
    assert (
        mock_run_hooks.call_args[1]["final_status"]
        == models.TaskStatus.COMPLETED
    )


@mock.patch("lemming.orchestrator.run_hooks")
@mock.patch("time.sleep", return_value=None)
def test_handle_runner_exit_completes(mock_sleep, mock_run_hooks, setup_env):
    test_tasks_file, initial_data = setup_env
    # A task requesting completion runs hooks and doesn't abort loop
    data = persistence.load_tasks(test_tasks_file)
    task = data.tasks[0]
    task.status = models.TaskStatus.IN_PROGRESS
    persistence.save_tasks(test_tasks_file, data)

    # Simulate agent calling `lemming complete` (finish_task_attempt
    # checks requested_status)
    operations.update_task(
        test_tasks_file, task.id, status=models.TaskStatus.COMPLETED
    )

    should_abort = _handle_runner_exit(
        test_tasks_file,
        task.id,
        returncode=0,
        stdout="done",
        stderr="",
        retries=3,
        retry_delay=5,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=[],
        working_dir=None,
        time_limit=1,
    )
    assert not should_abort


@mock.patch("lemming.orchestrator.run_hooks")
def test_handle_runner_exit_stops_on_unexpected_sigterm(
    mock_run_hooks, setup_env
):
    test_tasks_file, initial_data = setup_env
    data = persistence.load_tasks(test_tasks_file)
    task = data.tasks[0]
    task.status = models.TaskStatus.IN_PROGRESS
    persistence.save_tasks(test_tasks_file, data)

    should_abort = _handle_runner_exit(
        test_tasks_file,
        task.id,
        returncode=-15,
        stdout="",
        stderr="",
        retries=3,
        retry_delay=5,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=[],
        working_dir=None,
        time_limit=1,
    )
    assert should_abort


@mock.patch("lemming.orchestrator.run_hooks")
def test_handle_runner_exit_continues_after_cancellation(
    mock_run_hooks, setup_env
):
    test_tasks_file, initial_data = setup_env
    data = persistence.load_tasks(test_tasks_file)
    task = data.tasks[0]
    task.status = models.TaskStatus.CANCELLED
    persistence.save_tasks(test_tasks_file, data)

    should_abort = _handle_runner_exit(
        test_tasks_file,
        task.id,
        returncode=-15,
        stdout="",
        stderr="",
        retries=3,
        retry_delay=5,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=[],
        working_dir=None,
        time_limit=1,
    )

    assert not should_abort


@mock.patch("lemming.orchestrator.time.sleep")
def test_handle_runner_exit_stops_on_runner_failure(
    mock_sleep, setup_env, capsys
):
    test_tasks_file, initial_data = setup_env
    task = lifecycle.claim_task(test_tasks_file, "task1", pid=os.getpid())
    assert task is not None
    assert task.attempts == 1

    should_abort = _handle_runner_exit(
        test_tasks_file,
        task.id,
        returncode=1,
        stdout="provider unavailable",
        stderr="",
        retries=3,
        retry_delay=10,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=[],
        working_dir=None,
        time_limit=1,
    )

    assert should_abort
    mock_sleep.assert_not_called()
    updated = persistence.load_tasks(test_tasks_file).tasks[0]
    assert updated.status == models.TaskStatus.PENDING
    assert updated.attempts == 0
    output = capsys.readouterr().out
    assert "provider unavailable" in output
    assert "switch runners or retry later" in output


@pytest.fixture
def hooks_env(tmp_path, monkeypatch):
    """An isolated project with a single task for run_hooks tests."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    tasks_file = tmp_path / "tasks_test.yml"
    data = models.Roadmap(
        goal="Initial goal",
        tasks=[
            models.Task(
                id="12345678",
                description="Initial Task",
                status=models.TaskStatus.PENDING,
                attempts=0,
                progress=[],
            )
        ],
        config=models.RoadmapConfig(retries=3, runner="agy"),
    )
    persistence.save_tasks(tasks_file, data)
    return tasks_file


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("lemming.prompts.prepare_hook_prompt")
def test_run_hooks_success(mock_prepare, mock_run, hooks_env):
    mock_prepare.return_value = "Hook Prompt"
    mock_run.return_value = (0, "stdout", "")
    working_dir = hooks_env.parent / "project with spaces"

    # Task must be IN_PROGRESS for finalization to apply
    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.IN_PROGRESS
    )

    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=True,
        working_dir=working_dir,
        final_status=models.TaskStatus.COMPLETED,
    )

    assert mock_run.called
    cmd = mock_run.call_args.args[0]
    assert cmd[cmd.index("--add-dir") + 1] == str(working_dir)
    data = persistence.load_tasks(hooks_env)
    assert data.tasks[0].status == models.TaskStatus.COMPLETED


@pytest.mark.parametrize(
    ("config_hooks", "task_hooks", "expected"),
    [
        (["roadmap"], None, ["roadmap"]),
        (["ux", "roadmap"], None, ["ux", "roadmap"]),
        (["ux", "roadmap"], ["testing"], ["testing", "roadmap"]),
        (["ux", "roadmap"], [], ["roadmap"]),
        ([], ["testing"], ["testing"]),
    ],
)
@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("lemming.prompts.prepare_hook_prompt")
def test_run_hooks_selects_hooks_per_task(
    mock_prepare, mock_run, hooks_env, config_hooks, task_hooks, expected
):
    """Reviews are opt-in per project or task; the 9x band is project-wide."""
    mock_prepare.return_value = "Hook Prompt"
    mock_run.return_value = (0, "stdout", "")
    data = persistence.load_tasks(hooks_env)
    data.config.hooks = config_hooks
    data.tasks[0].hooks = task_hooks
    persistence.save_tasks(hooks_env, data)

    exit_codes = run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
    )

    assert list(exit_codes) == expected


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("lemming.prompts.prepare_hook_prompt")
def test_run_hooks_returns_exit_codes(mock_prepare, mock_run, hooks_env):
    mock_prepare.return_value = "Hook Prompt"
    mock_run.return_value = (1, "", "runner crashed")

    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.IN_PROGRESS
    )

    exit_codes = run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        hooks=["roadmap"],
        final_status=models.TaskStatus.COMPLETED,
    )

    assert exit_codes == {"roadmap": 1}


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("lemming.prompts.prepare_hook_prompt")
def test_run_hooks_returns_sentinel_on_runner_error(
    mock_prepare, mock_run, hooks_env
):
    mock_prepare.return_value = "Hook Prompt"
    mock_run.side_effect = OSError("no such binary")

    exit_codes = run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        hooks=["roadmap"],
    )

    assert exit_codes == {"roadmap": -1}


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_no_hooks(mock_run, hooks_env):
    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=True,
        hooks=[],
        final_status=models.TaskStatus.COMPLETED,
    )

    assert not mock_run.called
    data = persistence.load_tasks(hooks_env)
    assert data.tasks[0].status == models.TaskStatus.COMPLETED


def _exit_oneshot_task(tasks_file, active_hooks):
    """Runs the post-runner handling of a finalizing oneshot task."""
    return _handle_runner_exit(
        tasks_file,
        "12345678",
        returncode=0,
        stdout="",
        stderr="",
        retries=3,
        retry_delay=0,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=active_hooks,
        working_dir=None,
        time_limit=1,
    )


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_oneshot_task_completes_without_hooks(
    mock_run, mock_prepare, hooks_env
):
    operations.update_task(
        hooks_env,
        "12345678",
        oneshot=True,
        status=models.TaskStatus.IN_PROGRESS,
    )
    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.COMPLETED
    )

    should_abort = _exit_oneshot_task(hooks_env, ["readability", "roadmap"])

    assert not should_abort
    assert not mock_run.called
    data = persistence.load_tasks(hooks_env)
    assert data.tasks[0].status == models.TaskStatus.COMPLETED


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_oneshot_task_failure_still_runs_failure_hooks(
    mock_run, mock_prepare, hooks_env
):
    """The skip is scoped to success: an unconditional one loses recovery."""
    mock_run.return_value = (0, "stdout", "")
    mock_prepare.return_value = "Hook Prompt"
    operations.update_task(
        hooks_env,
        "12345678",
        oneshot=True,
        status=models.TaskStatus.IN_PROGRESS,
    )
    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.FAILED
    )

    _exit_oneshot_task(hooks_env, ["readability", "roadmap"])

    assert mock_prepare.call_count == 1
    assert mock_prepare.call_args[0][0] == "roadmap"


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_failure_filters_hooks(mock_run, mock_prepare, hooks_env):
    mock_run.return_value = (0, "stdout", "")
    mock_prepare.return_value = "Mock Prompt"

    # Task must be IN_PROGRESS for finalization to apply
    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.IN_PROGRESS
    )

    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=True,
        hooks=["readability", "roadmap", "testing"],
        final_status=models.TaskStatus.FAILED,
    )

    # It should only run 'roadmap' hook (priority 90, a failure hook)
    assert mock_prepare.call_count == 1
    assert mock_prepare.call_args[0][0] == "roadmap"


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_failure_runs_custom_failure_hooks(
    mock_run, mock_prepare, hooks_env
):
    """Hooks with a 9x priority prefix run on failure, others do not."""
    mock_run.return_value = (0, "stdout", "")
    mock_prepare.return_value = "Mock Prompt"

    # A custom failure hook (95) and a regular hook (20) in the project
    local_hooks_dir = hooks_env.parent / ".lemming" / "hooks"
    local_hooks_dir.mkdir(parents=True)
    (local_hooks_dir / "95-notify.md").write_text("n", encoding="utf-8")
    (local_hooks_dir / "20-lint.md").write_text("l", encoding="utf-8")
    data = persistence.load_tasks(hooks_env)
    data.config.hooks = ["roadmap", "notify", "lint"]
    persistence.save_tasks(hooks_env, data)

    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.IN_PROGRESS
    )

    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=True,
        final_status=models.TaskStatus.FAILED,
    )

    # Only the 9x hooks run, in priority order
    ran = [call[0][0] for call in mock_prepare.call_args_list]
    assert ran == ["roadmap", "notify"]


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_skips_finalization_when_healed(
    mock_run, mock_prepare, hooks_env
):
    """If a hook resets the task (heals it), skip finalization."""
    mock_prepare.return_value = "Mock Prompt"

    # Task must be IN_PROGRESS for hooks to run
    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.IN_PROGRESS
    )

    # Simulate the hook resetting the task during execution
    def hook_resets_task(*args, **kwargs):
        lifecycle.reset_task(hooks_env, "12345678")
        return (0, "stdout", "")

    mock_run.side_effect = hook_resets_task

    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=True,
        hooks=["roadmap"],
        final_status=models.TaskStatus.FAILED,
    )

    # Task should remain PENDING (healed), not FAILED
    data = persistence.load_tasks(hooks_env)
    assert data.tasks[0].status == models.TaskStatus.PENDING
    assert data.tasks[0].attempts == 0


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_reverts_to_pending_when_hook_killed(
    mock_run, mock_prepare, hooks_env
):
    """A killed/failed finalization hook must not mark the task completed."""
    mock_prepare.return_value = "Mock Prompt"
    # Simulate the hook runner being killed (e.g. model became unavailable).
    mock_run.return_value = (-15, "", "")

    # Put the task in the finalizing state: in progress with a requested
    # completion, as if the agent had called `lemming complete`.
    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.IN_PROGRESS
    )
    lifecycle.claim_task(hooks_env, "12345678", pid=os.getpid())
    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.COMPLETED
    )

    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        hooks=["roadmap"],
        final_status=models.TaskStatus.COMPLETED,
    )

    # The task should be retried from scratch, keeping its attempt count.
    data = persistence.load_tasks(hooks_env)
    task = data.tasks[0]
    assert task.status == models.TaskStatus.PENDING
    assert task.requested_status is None
    assert task.attempts == 1
    assert any("hook" in p.lower() for p in task.progress)


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_caps_time_limit(mock_run, mock_prepare, hooks_env):
    """A hook gets at most HOOK_TIME_LIMIT, not the task's whole budget."""
    mock_prepare.return_value = "Mock Prompt"
    mock_run.return_value = (0, "", "")

    for time_limit, expected in [
        (60, HOOK_TIME_LIMIT),
        (0, 0),
        (5, 5),
    ]:
        run_hooks(
            hooks_env,
            "12345678",
            "agy",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=False,
            hooks=["roadmap"],
            time_limit=time_limit,
        )
        _, kwargs = mock_run.call_args
        assert kwargs["time_limit"] == expected


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_reverts_to_pending_when_a_hook_rejects(
    mock_run, mock_prepare, hooks_env
):
    """A hook that exits cleanly can still refuse the completion."""
    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.IN_PROGRESS
    )
    lifecycle.claim_task(hooks_env, "12345678", pid=os.getpid())
    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.COMPLETED
    )

    # The first hook rejects; the rest must still run and see the rejection.
    seen_prompts = []

    def run_hook(*args, **kwargs):
        if kwargs["header"] == "Hook: testing":
            lifecycle.reject_task(hooks_env, "12345678", "suite fails")
        return (0, "", "")

    def prepare_prompt(hook_name, data, task, *args, **kwargs):
        seen_prompts.append((hook_name, task.rejection))
        return "Mock Prompt"

    mock_run.side_effect = run_hook
    mock_prepare.side_effect = prepare_prompt

    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        hooks=["testing", "roadmap"],
        final_status=models.TaskStatus.COMPLETED,
    )

    assert seen_prompts == [("testing", None), ("roadmap", "suite fails")]

    task = persistence.load_tasks(hooks_env).tasks[0]
    assert task.status == models.TaskStatus.PENDING
    assert task.requested_status is None
    assert task.rejection is None
    assert task.attempts == 1
    assert any(
        "Hook 'testing' rejected completion: suite fails" in entry
        for entry in task.progress
    )


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_failure_finalization_ignores_hook_errors(
    mock_run, mock_prepare, hooks_env
):
    """Failed hooks must not revert a task that is being marked FAILED."""
    mock_prepare.return_value = "Mock Prompt"
    mock_run.return_value = (1, "", "")

    operations.update_task(
        hooks_env, "12345678", status=models.TaskStatus.IN_PROGRESS
    )

    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        hooks=["roadmap"],
        final_status=models.TaskStatus.FAILED,
    )

    data = persistence.load_tasks(hooks_env)
    assert data.tasks[0].status == models.TaskStatus.FAILED


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_reloads_tasks(mock_run, mock_prepare, hooks_env):
    # Create a real Roadmap object to return
    real_data = persistence.load_tasks(hooks_env)
    mock_run.return_value = (0, "stdout", "")
    mock_prepare.return_value = "Mock Prompt"

    with mock.patch(
        "lemming.persistence.load_tasks", return_value=real_data
    ) as mock_load:
        run_hooks(
            hooks_env,
            "12345678",
            "agy",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=True,
            hooks=["h1", "h2"],
        )

        # 1 initial load + per hook: 1 for context, 1 for its rejection.
        assert mock_load.call_count == 5


def _complete_current_task(tasks_file):
    """Builds a runner stub that marks whichever task it runs as completed."""

    def side_effect(cmd, tasks_file_arg, task_id, *args, **kwargs):
        with persistence.lock_tasks(tasks_file):
            data = persistence.load_tasks(tasks_file)
            task = next(t for t in data.tasks if t.id == task_id)
            task.status = models.TaskStatus.COMPLETED
            task.completed_at = time.time()
            persistence.save_tasks(tasks_file, data)
        return (0, "output", "")

    return side_effect


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_loop_max_tasks_stops_between_tasks(mock_run, setup_env, capsys):
    """A milestone limit exits cleanly and leaves the queue intact."""
    test_tasks_file, _ = setup_env
    operations.add_task(test_tasks_file, "Task 2")
    operations.add_task(test_tasks_file, "Task 3")
    mock_run.side_effect = _complete_current_task(test_tasks_file)

    completed = run_loop(
        test_tasks_file,
        verbose=False,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
        hooks=[],
        max_tasks=2,
    )

    assert completed is True
    statuses = [t.status for t in persistence.load_tasks(test_tasks_file).tasks]
    assert statuses == [
        models.TaskStatus.COMPLETED,
        models.TaskStatus.COMPLETED,
        models.TaskStatus.PENDING,
    ]
    assert "Stopped after 2 tasks; 1 pending" in capsys.readouterr().out


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_loop_max_tasks_counts_only_settled_tasks(
    mock_run, setup_env, capsys
):
    """An attempt that leaves the task pending does not consume the budget."""
    test_tasks_file, _ = setup_env
    operations.add_task(test_tasks_file, "Task 2")
    attempts = []

    def flaky_then_complete(cmd, tasks_file_arg, task_id, *args, **kwargs):
        attempts.append(task_id)
        if len(attempts) == 1:
            return (0, "no completion reported", "")
        return _complete_current_task(test_tasks_file)(
            cmd, tasks_file_arg, task_id, *args, **kwargs
        )

    mock_run.side_effect = flaky_then_complete

    completed = run_loop(
        test_tasks_file,
        verbose=False,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
        hooks=[],
        max_tasks=1,
    )

    assert completed is True
    assert attempts == ["task1", "task1"]
    statuses = [t.status for t in persistence.load_tasks(test_tasks_file).tasks]
    assert statuses == [models.TaskStatus.COMPLETED, models.TaskStatus.PENDING]


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_loop_until_stops_after_milestone_task(mock_run, setup_env, capsys):
    """The loop runs inserted tasks and stops once the target settles."""
    test_tasks_file, _ = setup_env
    milestone = operations.add_task(test_tasks_file, "Milestone")
    operations.add_task(test_tasks_file, "After milestone")
    mock_run.side_effect = _complete_current_task(test_tasks_file)

    completed = run_loop(
        test_tasks_file,
        verbose=False,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
        hooks=[],
        until=milestone.id,
    )

    assert completed is True
    statuses = [t.status for t in persistence.load_tasks(test_tasks_file).tasks]
    assert statuses == [
        models.TaskStatus.COMPLETED,
        models.TaskStatus.COMPLETED,
        models.TaskStatus.PENDING,
    ]
    assert f"Reached milestone {milestone.id}; 1 pending" in (
        capsys.readouterr().out
    )


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_loop_until_stops_when_milestone_task_vanishes(
    mock_run, setup_env, capsys
):
    """Deleting the target mid-run is reported instead of running the queue."""
    test_tasks_file, _ = setup_env
    milestone = operations.add_task(test_tasks_file, "Milestone")

    def complete_and_delete(cmd, tasks_file_arg, task_id, *args, **kwargs):
        operations.delete_tasks(test_tasks_file, milestone.id)
        return _complete_current_task(test_tasks_file)(
            cmd, tasks_file_arg, task_id, *args, **kwargs
        )

    mock_run.side_effect = complete_and_delete

    completed = run_loop(
        test_tasks_file,
        verbose=False,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
        hooks=[],
        until=milestone.id,
    )

    assert completed is False
    assert mock_run.call_count == 1
    assert f"Milestone task {milestone.id} no longer exists" in (
        capsys.readouterr().out
    )


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_loop_records_resolved_command(mock_run, setup_env):
    """Provenance is persisted so the model behind a commit is recoverable."""
    test_tasks_file, initial_data = setup_env
    data = persistence.load_tasks(test_tasks_file)
    data.config.model = "pinned-model"
    persistence.save_tasks(test_tasks_file, data)
    mock_run.return_value = (0, "output", "")

    mock_task = initial_data.tasks[0]
    mock_task.status = models.TaskStatus.COMPLETED
    with mock.patch(
        "lemming.tasks.lifecycle.finish_task_attempt", return_value=mock_task
    ):
        run_loop(
            test_tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
        )

    recorded = persistence.load_tasks(test_tasks_file).tasks[0].resolved_command
    assert recorded is not None
    assert "--model pinned-model" in recorded
    assert recorded.startswith("agy ")


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_loop_task_model_overrides_project_model(mock_run, setup_env):
    """A per-task model beats the project-wide pin."""
    test_tasks_file, initial_data = setup_env
    data = persistence.load_tasks(test_tasks_file)
    data.config.model = "project-model"
    data.tasks[0].model = "task-model"
    persistence.save_tasks(test_tasks_file, data)
    mock_run.return_value = (0, "output", "")

    mock_task = initial_data.tasks[0]
    mock_task.status = models.TaskStatus.COMPLETED
    with mock.patch(
        "lemming.tasks.lifecycle.finish_task_attempt", return_value=mock_task
    ):
        run_loop(
            test_tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
        )

    cmd = mock_run.call_args[0][0]
    assert cmd.count("--model") == 1
    assert cmd[cmd.index("--model") + 1] == "task-model"


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("time.sleep", return_value=None)
def test_run_loop_surfaces_runner_error(
    mock_sleep, mock_run, setup_env, capsys
):
    """A failing runner's own reason must not stay buried in the log."""
    test_tasks_file, _ = setup_env
    mock_run.return_value = (
        1,
        '{"type":"error","message":"You\'ve hit your usage limit."}\n',
        "",
    )

    run_loop(
        test_tasks_file,
        verbose=False,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    assert "You've hit your usage limit." in capsys.readouterr().out
    progress = persistence.load_tasks(test_tasks_file).tasks[0].progress
    assert any("usage limit" in entry for entry in progress), progress


@mock.patch("time.sleep", return_value=None)
def test_run_loop_does_not_dump_the_log_of_a_successful_task(
    mock_sleep, setup_env, capsys
):
    """A task that finished is reported, not replayed.

    The agent requests completion and hooks apply it, so the status read
    right after the runner exits still says in-progress. Treating that as
    a failure replays the whole runner log on every successful task.
    """
    test_tasks_file, _ = setup_env

    def fake(cmd, tasks_file, task_id, verbose, **kwargs):
        progress.add_progress(tasks_file, task_id, "did the thing")
        operations.update_task(tasks_file, task_id, status="completed")
        return 0, "VERBATIM RUNNER LOG LINE\n" * 3, ""

    with mock.patch("lemming.runner.run_with_heartbeat", fake):
        completed = run_loop(
            test_tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
            hooks=[],
        )

    out = capsys.readouterr().out
    assert completed
    assert "VERBATIM RUNNER LOG LINE" not in out
    assert "Task completed successfully" in out


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("time.sleep", return_value=None)
def test_run_loop_failure_without_a_reason_is_unchanged(
    mock_sleep, mock_run, setup_env, capsys
):
    """Runners that say nothing still get the generic notice."""
    test_tasks_file, _ = setup_env
    mock_run.return_value = (1, "no structured output here\n", "")

    run_loop(
        test_tasks_file,
        verbose=False,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    out = capsys.readouterr().out
    assert "Runner exited unsuccessfully" in out
    assert "Runner reported:" not in out


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_surfaces_hook_error(mock_run, setup_env, capsys):
    """A failing hook's reason should not require opening the log."""
    test_tasks_file, _ = setup_env
    lifecycle.mark_task_in_progress(test_tasks_file, "task1")
    mock_run.return_value = (
        1,
        '{"type":"error","message":"You\'ve hit your usage limit."}\n',
        "",
    )

    exit_codes = run_hooks(
        test_tasks_file,
        "task1",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        hooks=["roadmap"],
    )

    assert exit_codes["roadmap"] == 1
    assert "You've hit your usage limit." in capsys.readouterr().out


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_quiet_when_hook_succeeds(mock_run, setup_env, capsys):
    """A zero exit code stays silent, as before."""
    test_tasks_file, _ = setup_env
    lifecycle.mark_task_in_progress(test_tasks_file, "task1")
    mock_run.return_value = (0, '{"type":"error","message":"ignored"}\n', "")

    run_hooks(
        test_tasks_file,
        "task1",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        hooks=["roadmap"],
    )

    assert "reported:" not in capsys.readouterr().out


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_propagates_corrupted_tasks(mock_run, setup_env, capsys):
    """An unreadable roadmap is not a hook failure to report and move past."""
    test_tasks_file, _ = setup_env
    lifecycle.mark_task_in_progress(test_tasks_file, "task1")
    mock_run.side_effect = persistence.CorruptedTasksError(
        test_tasks_file, ValueError("boom")
    )

    with pytest.raises(persistence.CorruptedTasksError):
        run_hooks(
            test_tasks_file,
            "task1",
            "agy",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=False,
            hooks=["roadmap"],
        )

    assert "Hook 'roadmap' error" not in capsys.readouterr().out


@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_loop_propagates_corrupted_tasks(mock_run, setup_env, capsys):
    """The same holds for the task runner: stop while the file is intact."""
    test_tasks_file, _ = setup_env
    mock_run.side_effect = persistence.CorruptedTasksError(
        test_tasks_file, ValueError("boom")
    )

    with pytest.raises(persistence.CorruptedTasksError):
        run_loop(
            test_tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
        )

    assert "An error occurred while executing" not in capsys.readouterr().out


def test_run_loop_records_snapshots_and_passes_changed_files_scope(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "app.py").write_text("v1\n", encoding="utf-8")

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[models.Task(id="t1", description="Update app")],
            config=models.RoadmapConfig(
                retries=2, runner="agy", hooks=["readability"]
            ),
        ),
    )

    def fake_run(cmd, t_file, t_id, verbose, **kwargs):
        header = kwargs.get("header") or ""
        if header.startswith("Hook:"):
            (work / "app.py").write_text(
                "v2\nformatted = True\n", encoding="utf-8"
            )
            return 0, "", ""
        (work / "app.py").write_text("v2\n", encoding="utf-8")
        (work / "extra.py").write_text("x = 1\n", encoding="utf-8")
        progress.add_progress(t_file, t_id, "updated app")
        operations.update_task(t_file, t_id, status=models.TaskStatus.COMPLETED)
        return 0, "", ""

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch(
            "lemming.prompts.prepare_hook_prompt",
            wraps=prompts.prepare_hook_prompt,
        ) as mock_prepare,
        mock.patch("time.sleep", return_value=None),
    ):
        assert run_loop(
            tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
            working_dir=work,
        )

    mock_prepare.assert_called_once()
    seen_scope = mock_prepare.call_args.kwargs["scope"]
    assert seen_scope is not None
    assert "- app.py" in seen_scope
    assert "- extra.py" in seen_scope

    task = persistence.load_tasks(tasks_file).tasks[0]
    assert task.status == models.TaskStatus.COMPLETED
    assert task.pre_tree is not None
    assert task.runner_tree is not None
    assert task.post_tree is not None
    assert task.runner_tree != task.pre_tree
    assert task.post_tree != task.runner_tree
    assert task.hook_trees == {
        "readability": [task.runner_tree, task.post_tree]
    }
    assert (
        task.diff_summary == "2 files (+3/-1): app.py (+2/-1), extra.py (+1/-0)"
    )
    assert (work / "app.py").read_text(
        encoding="utf-8"
    ) == "v2\nformatted = True\n"


def test_run_loop_reverts_workspace_and_writes_patch_on_incomplete_attempt(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    git_env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(
        ["git", "init", "--quiet", str(work)], env=git_env, check=True
    )
    subprocess.run(
        ["git", "-C", str(work), "config", "commit.gpgsign", "false"],
        env=git_env,
        check=True,
    )
    (work / "app.py").write_text("v1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(work), "add", "-A"], env=git_env, check=True
    )
    subprocess.run(
        ["git", "-C", str(work), "commit", "-q", "-m", "init"],
        env=git_env,
        check=True,
    )
    initial_head = snapshots.head_commit(work)
    assert initial_head is not None

    tasks_file = work / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[
                models.Task(
                    id="t1",
                    description="Flaky task",
                    hook_trees={"stale": ["aaa", "bbb"]},
                )
            ],
            config=models.RoadmapConfig(retries=2, runner="agy", hooks=[]),
        ),
    )

    attempt_counter = 0

    def fake_run(cmd, t_file, t_id, verbose, **kwargs):
        nonlocal attempt_counter
        attempt_counter += 1
        assert persistence.load_tasks(t_file).tasks[0].hook_trees is None
        if attempt_counter == 1:
            (work / "app.py").write_text("broken\n", encoding="utf-8")
            (work / "stray.txt").write_text("stray\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(work), "add", "-A"], env=git_env, check=True
            )
            subprocess.run(
                ["git", "-C", str(work), "commit", "-q", "-m", "bad"],
                env=git_env,
                check=True,
            )
            return RETURNCODE_TIMEOUT, "", ""

        # Attempt 2 must see the restored workspace and initial HEAD.
        assert (work / "app.py").read_text(encoding="utf-8") == "v1\n"
        assert not (work / "stray.txt").exists()
        assert snapshots.head_commit(work) == initial_head
        (work / "app.py").write_text("clean_v2\n", encoding="utf-8")
        progress.add_progress(t_file, t_id, "fixed on retry")
        operations.update_task(t_file, t_id, status=models.TaskStatus.COMPLETED)
        return 0, "", ""

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch("time.sleep", return_value=None),
    ):
        assert run_loop(
            tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
            working_dir=work,
            hooks=[],
        )

    patch_file = paths.get_artifacts_dir(tasks_file, "t1") / "attempt-1.patch"
    assert patch_file.is_file()
    assert "+broken" in patch_file.read_text(encoding="utf-8")
    task = persistence.load_tasks(tasks_file).tasks[0]
    assert task.pre_head == initial_head
    assert task.diff_summary == "1 file (+1/-1): app.py (+1/-1)"


def test_hooks_revert_nonzero_exit_and_exhausted_rejection_saves_final_patch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "app.py").write_text("v1\n", encoding="utf-8")

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[models.Task(id="t1", description="Task")],
            config=models.RoadmapConfig(
                retries=1, runner="agy", hooks=["testing", "roadmap"]
            ),
        ),
    )

    failure_hook_saw_app: list[str] = []

    def fake_run(cmd, t_file, t_id, verbose, **kwargs):
        header = kwargs.get("header") or ""
        if header == "Task Runner":
            (work / "app.py").write_text("rejected_v2\n", encoding="utf-8")
            progress.add_progress(t_file, t_id, "attempted")
            operations.update_task(
                t_file, t_id, status=models.TaskStatus.COMPLETED
            )
            return 0, "", ""
        if header == "Hook: testing":
            # Rejects completion and exits 0: changes stay on disk for retry,
            # until _process_exhausted_retries cleans up.
            lifecycle.reject_task(t_file, t_id, "tests failed")
            return 0, "", ""
        if header == "Hook: roadmap":
            data = persistence.load_tasks(t_file)
            t = data.tasks[0]
            if t.requested_status == models.TaskStatus.COMPLETED:
                # Simulate a crashing hook mutating the tree; must be reverted
                # to hook_pre_tree (which still has rejected_v2).
                (work / "crashed_hook.txt").write_text(
                    "bad\n", encoding="utf-8"
                )
                return 1, "", ""
            failure_hook_saw_app.append(
                (work / "app.py").read_text(encoding="utf-8")
            )
            return 0, "", ""
        return 0, "", ""

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch("time.sleep", return_value=None),
    ):
        completed = run_loop(
            tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
            working_dir=work,
        )

    assert completed is False
    # Non-zero hook edit was reverted immediately.
    assert not (work / "crashed_hook.txt").exists()
    # Exhausted retries restored pre_tree before running failure hooks.
    assert failure_hook_saw_app == ["v1\n"]
    assert (work / "app.py").read_text(encoding="utf-8") == "v1\n"
    final_patch = (
        paths.get_artifacts_dir(tasks_file, "t1") / "attempt-final.patch"
    )
    assert final_patch.is_file()
    assert "+rejected_v2" in final_patch.read_text(encoding="utf-8")


def test_rejected_attempt_followed_by_runner_failure_captures_full_patch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "a.py").write_text("v1\n", encoding="utf-8")
    (work / "b.py").write_text("b1\n", encoding="utf-8")

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[models.Task(id="t1", description="Task")],
            config=models.RoadmapConfig(
                retries=2, runner="agy", hooks=["testing", "roadmap"]
            ),
        ),
    )

    runner_calls = 0
    failure_prompts: list[str] = []

    def fake_run(cmd, t_file, t_id, verbose, **kwargs):
        nonlocal runner_calls
        header = kwargs.get("header") or ""
        if header == "Task Runner":
            runner_calls += 1
            if runner_calls == 1:
                (work / "a.py").write_text("attempt1_a\n", encoding="utf-8")
                progress.add_progress(t_file, t_id, "attempt 1")
                operations.update_task(
                    t_file, t_id, status=models.TaskStatus.COMPLETED
                )
                return 0, "", ""
            # Attempt 2 modifies b.py on top of attempt 1's rejected changes,
            # then times out without completing.
            assert (work / "a.py").read_text(encoding="utf-8") == "attempt1_a\n"
            (work / "b.py").write_text("attempt2_b\n", encoding="utf-8")
            return RETURNCODE_TIMEOUT, "", ""
        if header == "Hook: testing":
            lifecycle.reject_task(t_file, t_id, "tests failed")
            return 0, "", ""
        if header == "Hook: roadmap":
            data = persistence.load_tasks(t_file)
            t = data.tasks[0]
            if t.requested_status == models.TaskStatus.FAILED:
                assert t.runner_tree is None
                assert t.hook_trees is None
                failure_prompts.append(
                    prompts.prepare_hook_prompt("roadmap", data, t, t_file)
                )
            return 0, "", ""
        return 0, "", ""

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch("time.sleep", return_value=None),
    ):
        completed = run_loop(
            tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
            working_dir=work,
        )

    assert completed is False
    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"
    assert (work / "b.py").read_text(encoding="utf-8") == "b1\n"
    patch2 = paths.get_artifacts_dir(tasks_file, "t1") / "attempt-2.patch"
    assert patch2.is_file()
    patch2_text = patch2.read_text(encoding="utf-8")
    assert "+attempt1_a" in patch2_text
    assert "+attempt2_b" in patch2_text
    assert len(failure_prompts) == 1
    assert "Changes: 0 files changed\n" in failure_prompts[0]
    task = persistence.load_tasks(tasks_file).tasks[0]
    assert task.runner_tree is None
    assert "testing" not in (task.hook_trees or {})


def test_failed_task_clears_runner_tree_and_reset_keeps_hook_trees_none(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "app.py").write_text("v1\n", encoding="utf-8")

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[models.Task(id="t1", description="Task")],
            config=models.RoadmapConfig(
                retries=2, runner="agy", hooks=["roadmap"]
            ),
        ),
    )

    seen_prompts: list[str] = []
    runner_calls = 0

    def fake_run(cmd, t_file, t_id, verbose, **kwargs):
        nonlocal runner_calls
        header = kwargs.get("header") or ""
        if header == "Task Runner":
            runner_calls += 1
            if runner_calls == 1:
                (work / "app.py").write_text("failed_edit\n", encoding="utf-8")
                progress.add_progress(t_file, t_id, "cannot proceed")
                operations.update_task(
                    t_file, t_id, status=models.TaskStatus.FAILED
                )
                return 0, "", ""
            return 0, "", ""
        if header == "Hook: roadmap":
            data = persistence.load_tasks(t_file)
            t = data.tasks[0]
            assert t.runner_tree is None
            seen_prompts.append(
                prompts.prepare_hook_prompt("roadmap", data, t, t_file)
            )
            lifecycle.reset_task(t_file, t_id)
            return 0, "", ""
        return 0, "", ""

    t0 = snapshots.snapshot_tree(tasks_file, work)
    data = persistence.load_tasks(tasks_file)
    data.tasks[0].status = models.TaskStatus.IN_PROGRESS
    data.tasks[0].attempts = 1
    data.tasks[0].pre_tree = t0
    data.tasks[0].progress = ["cannot proceed"]
    data.tasks[0].requested_status = models.TaskStatus.FAILED
    persistence.save_tasks(tasks_file, data)
    (work / "app.py").write_text("failed_edit\n", encoding="utf-8")

    with mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run):
        should_abort = _handle_runner_exit(
            tasks_file=tasks_file,
            task_id="t1",
            returncode=0,
            stdout="",
            stderr="",
            retries=2,
            retry_delay=0,
            runner_name="agy",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=False,
            active_hooks=["roadmap"],
            working_dir=work,
            time_limit=1,
            attempt_pre_tree=t0,
        )

    assert not should_abort
    assert (work / "app.py").read_text(encoding="utf-8") == "v1\n"
    assert len(seen_prompts) == 1
    assert "Changes: 0 files changed\n" in seen_prompts[0]
    task = persistence.load_tasks(tasks_file).tasks[0]
    assert task.status == models.TaskStatus.PENDING
    assert task.runner_tree is None
    assert task.hook_trees is None


def test_cancellation_during_hooks_restores_once_and_suppresses_stdout(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    monkeypatch.setattr(snapshots, "head_commit", lambda _w: None)
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "app.py").write_text("v1\n", encoding="utf-8")
    t0 = snapshots.snapshot_tree(tasks_file, work)
    assert t0 is not None

    (work / "app.py").write_text("runner_v2\n", encoding="utf-8")
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[
                models.Task(
                    id="t1",
                    description="Cancel in hook",
                    status=models.TaskStatus.IN_PROGRESS,
                    attempts=1,
                    pre_tree=t0,
                    progress=["done"],
                    requested_status=models.TaskStatus.COMPLETED,
                )
            ],
            config=models.RoadmapConfig(
                retries=2, runner="agy", hooks=["testing", "roadmap"]
            ),
        ),
    )

    hook_calls: list[str] = []

    def fake_run(cmd, t_file, t_id, verbose, **kwargs):
        header = kwargs.get("header") or ""
        hook_calls.append(header)
        lifecycle.cancel_task(t_file, t_id, working_dir=work)
        # User edit after cancel_task restore must not be overwritten.
        (work / "app.py").write_text("user_edit\n", encoding="utf-8")
        return -15, "", ""

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch(
            "lemming.prompts.prepare_hook_prompt", return_value="Prompt"
        ),
    ):
        should_abort = _handle_runner_exit(
            tasks_file=tasks_file,
            task_id="t1",
            returncode=0,
            stdout="NOISY RUNNER STDOUT",
            stderr="NOISY RUNNER STDERR",
            retries=2,
            retry_delay=0,
            runner_name="agy",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=False,
            active_hooks=["testing", "roadmap"],
            working_dir=work,
            time_limit=1,
            attempt_pre_tree=t0,
        )

    assert should_abort is False
    assert hook_calls == ["Hook: testing"]
    assert (work / "app.py").read_text(encoding="utf-8") == "user_edit\n"
    captured = capsys.readouterr()
    assert "NOISY RUNNER STDOUT" not in captured.out
    assert "NOISY RUNNER STDERR" not in captured.err


def test_run_loop_restores_workspace_on_keyboard_interrupted_runner(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    monkeypatch.setattr(snapshots, "head_commit", lambda _w: None)
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "app.py").write_text("v1\n", encoding="utf-8")
    t0 = snapshots.snapshot_tree(tasks_file, work)
    assert t0 is not None
    # Simulate a prior rejected attempt leaving t1 on disk while pre_tree is t0.
    (work / "app.py").write_text("rejected_v1\n", encoding="utf-8")
    t1 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[
                models.Task(
                    id="t1",
                    description="Interrupted",
                    attempts=1,
                    pre_tree=t0,
                    runner_tree=t1,
                    hook_trees={"testing": [t1, t1]},
                )
            ],
            config=models.RoadmapConfig(retries=3, runner="agy", hooks=[]),
        ),
    )

    def fake_run(cmd, t_file, t_id, verbose, **kwargs):
        (work / "app.py").write_text("interrupted_v2\n", encoding="utf-8")
        progress.add_progress(t_file, t_id, "completed just before ctrl+c")
        operations.update_task(t_file, t_id, status=models.TaskStatus.COMPLETED)
        raise KeyboardInterrupt

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch("time.sleep", return_value=None),
        pytest.raises(KeyboardInterrupt),
    ):
        run_loop(
            tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
            working_dir=work,
            hooks=[],
        )

    assert (work / "app.py").read_text(encoding="utf-8") == "v1\n"
    patch_file = paths.get_artifacts_dir(tasks_file, "t1") / "attempt-2.patch"
    assert patch_file.is_file()
    assert "+interrupted_v2" in patch_file.read_text(encoding="utf-8")
    task = persistence.load_tasks(tasks_file).tasks[0]
    assert task.status == models.TaskStatus.PENDING
    assert task.requested_status is None
    assert task.rejection is None
    assert task.pid is None
    assert task.last_heartbeat is None
    assert task.active_execution_component is None
    assert task.active_execution_started_at is None
    assert task.runner_tree is None
    assert task.hook_trees is None

    # If cancel_task already ran before KeyboardInterrupt, delegate without
    # overwriting post-cancel user edits or writing an attempt patch.
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[models.Task(id="t2", description="Cancel before interrupt")],
            config=models.RoadmapConfig(retries=2, runner="agy", hooks=[]),
        ),
    )

    def fake_cancel_then_interrupt(cmd, t_file, t_id, verbose, **kwargs):
        (work / "app.py").write_text("runner_dirty\n", encoding="utf-8")
        lifecycle.cancel_task(t_file, t_id, working_dir=work)
        (work / "app.py").write_text("user_edit\n", encoding="utf-8")
        raise KeyboardInterrupt

    with (
        mock.patch(
            "lemming.runner.run_with_heartbeat",
            side_effect=fake_cancel_then_interrupt,
        ),
        mock.patch("lemming.orchestrator.os.getpid", return_value=None),
        mock.patch("time.sleep", return_value=None),
        pytest.raises(KeyboardInterrupt),
    ):
        run_loop(
            tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
            working_dir=work,
            hooks=[],
        )

    assert (work / "app.py").read_text(encoding="utf-8") == "user_edit\n"
    assert not (
        paths.get_artifacts_dir(tasks_file, "t2") / "attempt-1.patch"
    ).exists()
    cancelled_task = persistence.load_tasks(tasks_file).tasks[0]
    assert cancelled_task.status == models.TaskStatus.CANCELLED
    assert cancelled_task.pre_tree is None


def test_run_loop_interrupted_attempt_does_not_charge_a_retry(
    setup_env, capsys
):
    test_tasks_file, _ = setup_env

    def fake_run(*_args, **_kwargs):
        raise KeyboardInterrupt

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch("time.sleep", return_value=None),
        pytest.raises(KeyboardInterrupt),
    ):
        run_loop(test_tasks_file, False, 0, True, False, ())

    assert "[task1] Attempt 1/3" in capsys.readouterr().out
    task = persistence.load_tasks(test_tasks_file).tasks[0]
    assert task.status == models.TaskStatus.PENDING
    assert task.attempts == 0

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch("time.sleep", return_value=None),
        pytest.raises(KeyboardInterrupt),
    ):
        run_loop(test_tasks_file, False, 0, True, False, ())

    assert "[task1] Attempt 1/3" in capsys.readouterr().out


def test_run_loop_interrupted_after_agent_failed_keeps_the_attempt(setup_env):
    test_tasks_file, _ = setup_env

    def fake_run(cmd, t_file, t_id, *_args, **_kwargs):
        progress.add_progress(t_file, t_id, "cannot be done")
        operations.update_task(t_file, t_id, status=models.TaskStatus.FAILED)
        raise KeyboardInterrupt

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch("time.sleep", return_value=None),
        pytest.raises(KeyboardInterrupt),
    ):
        run_loop(test_tasks_file, False, 0, True, False, ())

    assert persistence.load_tasks(test_tasks_file).tasks[0].attempts == 1


def test_run_loop_interrupted_reclaimed_task_refunds_its_attempt(
    setup_env, capsys
):
    test_tasks_file, initial_data = setup_env
    task = initial_data.tasks[0]
    task.status = models.TaskStatus.IN_PROGRESS
    task.attempts = 1
    task.pid = 999999
    task.last_heartbeat = time.time() - 3600
    persistence.save_tasks(test_tasks_file, initial_data)

    with (
        mock.patch(
            "lemming.runner.run_with_heartbeat", side_effect=KeyboardInterrupt
        ),
        mock.patch("time.sleep", return_value=None),
        pytest.raises(KeyboardInterrupt),
    ):
        run_loop(test_tasks_file, False, 0, True, False, ())

    assert "[task1] Attempt 1/3" in capsys.readouterr().out
    task = persistence.load_tasks(test_tasks_file).tasks[0]
    assert task.status == models.TaskStatus.PENDING
    # Neither the abandoned run nor its interrupted resumption failed.
    assert task.attempts == 0


def test_finalizing_task_snapshots_runner_tree_and_once_restores_on_reject(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    monkeypatch.setattr(snapshots, "head_commit", lambda _w: None)
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "app.py").write_text("v1\n", encoding="utf-8")
    t0 = snapshots.snapshot_tree(tasks_file, work)
    assert t0 is not None

    (work / "app.py").write_text("resumed_v2\n", encoding="utf-8")
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[
                models.Task(
                    id="t1",
                    description="Resumed finalizing",
                    status=models.TaskStatus.PENDING,
                    attempts=0,
                    pre_tree=t0,
                    runner_tree=None,
                    progress=["completed before crash"],
                    requested_status=models.TaskStatus.COMPLETED,
                )
            ],
            config=models.RoadmapConfig(
                retries=2, runner="agy", hooks=["testing"]
            ),
        ),
    )

    seen_scopes: list[str | None] = []

    def fake_run(cmd, t_file, t_id, verbose, **kwargs):
        t = persistence.load_tasks(t_file).tasks[0]
        assert t.runner_tree is not None
        lifecycle.reject_task(t_file, t_id, "broken")
        return 0, "", ""

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch(
            "lemming.prompts.prepare_hook_prompt",
            side_effect=lambda h, d, t, tf, scope=None: (
                seen_scopes.append(scope) or "Prompt"
            ),
        ),
        mock.patch("time.sleep", return_value=None),
    ):
        completed = run_loop(
            tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
            working_dir=work,
            once=True,
        )

    assert completed is False
    assert seen_scopes and seen_scopes[0] is not None
    assert "- app.py" in seen_scopes[0]
    assert (work / "app.py").read_text(encoding="utf-8") == "v1\n"
    final_patch = (
        paths.get_artifacts_dir(tasks_file, "t1") / "attempt-final.patch"
    )
    assert final_patch.is_file()
    assert "+resumed_v2" in final_patch.read_text(encoding="utf-8")

    # Reset during _process_finalizing_task triggers deferred prune.
    (work / "app.py").write_text("v2\n", encoding="utf-8")
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[
                models.Task(
                    id="t1",
                    description="Finalizing reset",
                    status=models.TaskStatus.IN_PROGRESS,
                    attempts=1,
                    pre_tree=t0,
                    progress=["failed"],
                    requested_status=models.TaskStatus.FAILED,
                )
            ],
            config=models.RoadmapConfig(
                retries=2, runner="agy", hooks=["roadmap"]
            ),
        ),
    )
    pruned: list[set[str]] = []
    monkeypatch.setattr(
        snapshots,
        "prune_snapshots",
        lambda tf, keep: pruned.append(set(keep)),
    )

    def fake_reset(cmd, tf, tid, verbose, **kw):
        lifecycle.reset_task(tf, tid)
        return 0, "", ""

    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_reset),
        mock.patch(
            "lemming.prompts.prepare_hook_prompt", return_value="Prompt"
        ),
    ):
        _process_finalizing_task(
            tasks_file=tasks_file,
            task_id="t1",
            requested_status=models.TaskStatus.FAILED,
            runner_name="agy",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=False,
            active_hooks=["roadmap"],
            working_dir=work,
            time_limit=1,
        )

    assert len(pruned) == 1
    assert (work / "app.py").read_text(encoding="utf-8") == "v1\n"

    # Cancelled during _process_finalizing_task restores pre_tree.
    (work / "app.py").write_text("v2\n", encoding="utf-8")
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[
                models.Task(
                    id="t1",
                    description="Finalizing cancel",
                    status=models.TaskStatus.IN_PROGRESS,
                    attempts=1,
                    pre_tree=t0,
                    progress=["done"],
                    requested_status=models.TaskStatus.COMPLETED,
                )
            ],
            config=models.RoadmapConfig(
                retries=2, runner="agy", hooks=["roadmap"]
            ),
        ),
    )

    def cancel_status_only(cmd, tf, tid, verbose, **kw):
        with persistence.lock_tasks(tf):
            d = persistence.load_tasks(tf)
            d.tasks[0].status = models.TaskStatus.CANCELLED
            persistence.save_tasks(tf, d)
        return 0, "", ""

    with (
        mock.patch(
            "lemming.runner.run_with_heartbeat", side_effect=cancel_status_only
        ),
        mock.patch(
            "lemming.prompts.prepare_hook_prompt", return_value="Prompt"
        ),
    ):
        _process_finalizing_task(
            tasks_file=tasks_file,
            task_id="t1",
            requested_status=models.TaskStatus.COMPLETED,
            runner_name="agy",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=False,
            active_hooks=["roadmap"],
            working_dir=work,
            time_limit=1,
        )

    assert (work / "app.py").read_text(encoding="utf-8") == "v1\n"
    task = persistence.load_tasks(tasks_file).tasks[0]
    assert task.pre_tree is None
    assert task.runner_tree is None


def test_uncounted_runner_failure_preserves_prior_rejected_attempt_tree(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    monkeypatch.setattr(snapshots, "head_commit", lambda _w: None)
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "a.py").write_text("v1\n", encoding="utf-8")
    (work / "b.py").write_text("b1\n", encoding="utf-8")
    t0 = snapshots.snapshot_tree(tasks_file, work)
    assert t0 is not None

    # Attempt 1 wrote attempt-1.patch and left rejected changes on disk (t1).
    (work / "a.py").write_text("attempt1_a\n", encoding="utf-8")
    t1 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None
    lifecycle.write_patch_artifact(tasks_file, "t1", "attempt-1.patch", t0, t1)

    # Attempt 2 starts from t1, mutates b.py, and fails with exit code 1.
    (work / "b.py").write_text("crashed_b\n", encoding="utf-8")
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[
                models.Task(
                    id="t1",
                    description="Uncounted fail",
                    status=models.TaskStatus.IN_PROGRESS,
                    attempts=2,
                    pre_tree=t0,
                    runner_tree=None,
                )
            ],
            config=models.RoadmapConfig(retries=3, runner="agy", hooks=[]),
        ),
    )

    should_abort = _handle_runner_exit(
        tasks_file=tasks_file,
        task_id="t1",
        returncode=1,
        stdout="",
        stderr="",
        retries=3,
        retry_delay=0,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=[],
        working_dir=work,
        time_limit=1,
        retry_runner_failures=False,
        attempt_pre_tree=t1,
    )

    assert should_abort is True
    # Workspace is restored to t1 (Attempt 1's rejected state), not t0!
    assert (work / "a.py").read_text(encoding="utf-8") == "attempt1_a\n"
    assert (work / "b.py").read_text(encoding="utf-8") == "b1\n"
    artifacts_dir = paths.get_artifacts_dir(tasks_file, "t1")
    patch1 = (artifacts_dir / "attempt-1.patch").read_text(encoding="utf-8")
    patch2 = (artifacts_dir / "attempt-2.patch").read_text(encoding="utf-8")
    assert "+crashed_b" not in patch1
    assert "+crashed_b" in patch2
    task = persistence.load_tasks(tasks_file).tasks[0]
    assert task.attempts == 1
    assert task.runner_tree == t1

    # If the runner explicitly called `lemming fail` (requested_status=FAILED)
    # before exiting 1, it is a counted finalizing failure, not uncounted.
    (work / "b.py").write_text("explicit_fail_b\n", encoding="utf-8")
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[
                models.Task(
                    id="t1",
                    description="Explicit fail then exit 1",
                    status=models.TaskStatus.IN_PROGRESS,
                    attempts=2,
                    pre_tree=t0,
                    runner_tree=t1,
                    progress=["gave up"],
                    requested_status=models.TaskStatus.FAILED,
                )
            ],
            config=models.RoadmapConfig(retries=3, runner="agy", hooks=[]),
        ),
    )

    should_abort_explicit = _handle_runner_exit(
        tasks_file=tasks_file,
        task_id="t1",
        returncode=1,
        stdout="",
        stderr="",
        retries=3,
        retry_delay=0,
        runner_name="agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        active_hooks=[],
        working_dir=work,
        time_limit=1,
        retry_runner_failures=False,
        attempt_pre_tree=t1,
    )

    assert should_abort_explicit is False
    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"
    assert (work / "b.py").read_text(encoding="utf-8") == "b1\n"
    assert not (artifacts_dir / "attempt-3.patch").exists()
    explicit_task = persistence.load_tasks(tasks_file).tasks[0]
    assert explicit_task.status == models.TaskStatus.FAILED
    assert explicit_task.attempts == 2
    assert explicit_task.runner_tree is None


def test_reset_task_in_hook_defers_prune_until_after_run_hooks(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    monkeypatch.setattr(snapshots, "head_commit", lambda _w: None)
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "app.py").write_text("v1\n", encoding="utf-8")
    t0 = snapshots.snapshot_tree(tasks_file, work)
    assert t0 is not None

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="Goal",
            tasks=[
                models.Task(
                    id="t1",
                    description="Reset in hook",
                    status=models.TaskStatus.IN_PROGRESS,
                    attempts=1,
                    pre_tree=t0,
                    progress=["failed"],
                    requested_status=models.TaskStatus.FAILED,
                )
            ],
            config=models.RoadmapConfig(
                retries=2, runner="agy", hooks=["roadmap"]
            ),
        ),
    )

    events: list[str] = []
    orig_prune = snapshots.prune_snapshots

    def tracking_prune(t_file, keep):
        events.append("prune")
        return orig_prune(t_file, keep)

    def fake_run(cmd, t_file, t_id, verbose, **kwargs):
        events.append("hook_start")
        lifecycle.reset_task(t_file, t_id)
        assert "prune" not in events
        # Hook failure after reset_task still restores intact hook_pre_tree.
        (work / "app.py").write_text("hook_dirty\n", encoding="utf-8")
        events.append("hook_end")
        return 1, "", ""

    monkeypatch.setattr(snapshots, "prune_snapshots", tracking_prune)
    with (
        mock.patch("lemming.runner.run_with_heartbeat", side_effect=fake_run),
        mock.patch(
            "lemming.prompts.prepare_hook_prompt", return_value="Prompt"
        ),
    ):
        should_abort = _handle_runner_exit(
            tasks_file=tasks_file,
            task_id="t1",
            returncode=0,
            stdout="",
            stderr="",
            retries=2,
            retry_delay=0,
            runner_name="agy",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=False,
            active_hooks=["roadmap"],
            working_dir=work,
            time_limit=1,
            attempt_pre_tree=t0,
        )

    assert should_abort is False
    assert events == ["hook_start", "hook_end", "prune"]
    assert (work / "app.py").read_text(encoding="utf-8") == "v1\n"
