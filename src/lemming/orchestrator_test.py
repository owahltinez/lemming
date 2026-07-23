import os
import time
from unittest import mock

import pytest

from lemming import tasks
from lemming.orchestrator import (
    _handle_runner_exit,
    _process_exhausted_retries,
    _process_finalizing_task,
    format_duration,
    parse_timeout,
    run_hooks,
    run_loop,
)
from lemming.runner import RETURNCODE_TIMEOUT


@pytest.fixture
def setup_env(tmp_path):
    test_tasks_file = tmp_path / "tasks_test.yml"

    # Scaffold a valid file with one task
    initial_data = tasks.Roadmap(
        goal="Initial goal",
        tasks=[
            tasks.Task(
                id="task1",
                description="Task 1",
                status=tasks.TaskStatus.PENDING,
                attempts=0,
                progress=[],
            )
        ],
        config=tasks.RoadmapConfig(retries=3, runner="agy"),
    )
    tasks.save_tasks(test_tasks_file, initial_data)
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


@mock.patch("subprocess.Popen")
def test_run_loop_success(mock_popen, setup_env):
    test_tasks_file, initial_data = setup_env
    # Simulate runner reporting success
    mock_process = mock.MagicMock()
    mock_process.pid = 12345
    mock_process.poll.side_effect = [None, 0]
    mock_process.returncode = 0
    mock_process.stdout = iter(["stdout\n"])
    mock_process.communicate.return_value = ("stdout", "stderr")

    def wait_side_effect():
        with tasks.lock_tasks(test_tasks_file):
            data = tasks.load_tasks(test_tasks_file)
            data.tasks[0].status = tasks.TaskStatus.COMPLETED
            data.tasks[0].completed_at = time.time()
            tasks.save_tasks(test_tasks_file, data)
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

    data = tasks.load_tasks(test_tasks_file)
    assert data.tasks[0].status == tasks.TaskStatus.COMPLETED


@mock.patch("subprocess.Popen")
@mock.patch("time.sleep", return_value=None)
def test_run_loop_retry_and_fail(mock_sleep, mock_popen, setup_env):
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
    data = tasks.load_tasks(test_tasks_file)
    data.config.retries = 2
    tasks.save_tasks(test_tasks_file, data)

    run_loop(
        test_tasks_file,
        verbose=True,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    data = tasks.load_tasks(test_tasks_file)
    assert data.tasks[0].attempts == 2
    assert data.tasks[0].status == tasks.TaskStatus.FAILED


def test_synchronous_hooks_execution_timing(setup_env):
    """Verifies a new task starts only AFTER previous hooks finished."""
    test_tasks_file, initial_data = setup_env
    # 1. Setup two pending tasks.
    now = time.time()
    with tasks.lock_tasks(test_tasks_file):
        data = tasks.load_tasks(test_tasks_file)
        data.tasks = [
            tasks.Task(
                id="task2",
                description="Task 2",
                status=tasks.TaskStatus.PENDING,
                created_at=now,
            ),
            tasks.Task(
                id="task1",
                description="Task 1",
                status=tasks.TaskStatus.PENDING,
                created_at=now + 10,
            ),
        ]
        data.config.retries = 1
        data.config.runner = "true"
        tasks.save_tasks(test_tasks_file, data)

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
        with tasks.lock_tasks(t_file):
            data = tasks.load_tasks(t_file)
            task = next(t for t in data.tasks if t.id == t_id)
            task.requested_status = tasks.TaskStatus.COMPLETED
            task.status = tasks.TaskStatus.IN_PROGRESS
            tasks.save_tasks(t_file, data)
            return task

    with (
        mock.patch(
            "lemming.runner.run_with_heartbeat",
            side_effect=mocked_run_with_heartbeat,
        ),
        mock.patch(
            "lemming.tasks.finish_task_attempt",
            side_effect=mocked_finish_task_attempt,
        ),
        mock.patch(
            "lemming.orchestrator.list_hooks", return_value=["test_hook"]
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
    # Setup runner mock
    mock_run.return_value = (0, "output", "")

    # Mock finish_task_attempt to return a completed task to end loop
    mock_task = initial_data.tasks[0]
    mock_task.status = tasks.TaskStatus.COMPLETED
    with mock.patch(
        "lemming.tasks.finish_task_attempt", return_value=mock_task
    ):
        run_loop(
            test_tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
        )

    # Verify run_with_heartbeat was called with header="Task Runner"
    args, kwargs = mock_run.call_args
    assert kwargs.get("header") == "Task Runner"


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("time.sleep", return_value=None)
def test_run_loop_cancelled(mock_sleep, mock_run, setup_env):
    test_tasks_file, initial_data = setup_env
    # Simulate a task being cancelled (exit code -15)
    mock_run.return_value = (-15, "cancelled", "error")

    # Configure retries to ensure it DOES NOT retry
    data = tasks.load_tasks(test_tasks_file)
    data.config.retries = 3
    tasks.save_tasks(test_tasks_file, data)

    run_loop(
        test_tasks_file,
        verbose=True,
        retry_delay=1,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    # It should only have 1 attempt
    data = tasks.load_tasks(test_tasks_file)
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

    data = tasks.load_tasks(test_tasks_file)
    data.config.retries = 2
    data.config.time_limit = 60
    tasks.save_tasks(test_tasks_file, data)

    run_loop(
        test_tasks_file,
        verbose=False,
        retry_delay=0,
        yolo=True,
        no_defaults=False,
        runner_args=(),
    )

    data = tasks.load_tasks(test_tasks_file)
    assert data.tasks[0].attempts == 2
    assert data.tasks[0].status == tasks.TaskStatus.FAILED


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("time.sleep", return_value=None)
def test_run_loop_passes_time_limit(mock_sleep, mock_run, setup_env):
    """Verifies that time_limit from config is passed to the runner."""
    test_tasks_file, initial_data = setup_env
    mock_run.return_value = (0, "output", "")

    data = tasks.load_tasks(test_tasks_file)
    data.config.time_limit = 30
    tasks.save_tasks(test_tasks_file, data)

    mock_task = initial_data.tasks[0]
    mock_task.status = tasks.TaskStatus.COMPLETED
    with mock.patch(
        "lemming.tasks.finish_task_attempt", return_value=mock_task
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
    data = tasks.load_tasks(test_tasks_file)
    task = data.tasks[0]
    task.attempts = 3
    tasks.save_tasks(test_tasks_file, data)

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
        mock_run_hooks.call_args[1]["final_status"] == tasks.TaskStatus.FAILED
    )


@mock.patch("lemming.orchestrator.run_hooks")
def test_process_exhausted_retries_healed(mock_run_hooks, setup_env):
    test_tasks_file, initial_data = setup_env

    # Setup task but simulate a hook healing it by resetting attempts
    def fake_run_hooks(*args, **kwargs):
        data = tasks.load_tasks(test_tasks_file)
        data.tasks[0].attempts = 0  # Healed!
        tasks.save_tasks(test_tasks_file, data)

    mock_run_hooks.side_effect = fake_run_hooks

    data = tasks.load_tasks(test_tasks_file)
    task = data.tasks[0]
    task.attempts = 3
    tasks.save_tasks(test_tasks_file, data)

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
def test_process_finalizing_task(mock_run_hooks, setup_env):
    test_tasks_file, initial_data = setup_env
    _process_finalizing_task(
        test_tasks_file,
        "task1",
        requested_status=tasks.TaskStatus.COMPLETED,
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
        == tasks.TaskStatus.COMPLETED
    )


@mock.patch("lemming.orchestrator.run_hooks")
@mock.patch("time.sleep", return_value=None)
def test_handle_runner_exit_completes(mock_sleep, mock_run_hooks, setup_env):
    test_tasks_file, initial_data = setup_env
    # A task requesting completion runs hooks and doesn't abort loop
    data = tasks.load_tasks(test_tasks_file)
    task = data.tasks[0]
    task.status = tasks.TaskStatus.IN_PROGRESS
    tasks.save_tasks(test_tasks_file, data)

    # Simulate agent calling `lemming complete` (finish_task_attempt
    # checks requested_status)
    tasks.update_task(
        test_tasks_file, task.id, status=tasks.TaskStatus.COMPLETED
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
def test_handle_runner_exit_cancelled(mock_run_hooks, setup_env):
    test_tasks_file, initial_data = setup_env
    data = tasks.load_tasks(test_tasks_file)
    task = data.tasks[0]
    task.status = tasks.TaskStatus.IN_PROGRESS
    tasks.save_tasks(test_tasks_file, data)

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
    assert should_abort, "Cancellation (-15) should abort the loop"


@pytest.fixture
def hooks_env(tmp_path, monkeypatch):
    """An isolated project with a single task for run_hooks tests."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    tasks_file = tmp_path / "tasks_test.yml"
    data = tasks.Roadmap(
        goal="Initial goal",
        tasks=[
            tasks.Task(
                id="12345678",
                description="Initial Task",
                status=tasks.TaskStatus.PENDING,
                attempts=0,
                progress=[],
            )
        ],
        config=tasks.RoadmapConfig(retries=3, runner="agy"),
    )
    tasks.save_tasks(tasks_file, data)
    return tasks_file


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("lemming.orchestrator.list_hooks")
@mock.patch("lemming.prompts.prepare_hook_prompt")
def test_run_hooks_success(mock_prepare, mock_list, mock_run, hooks_env):
    mock_list.return_value = ["roadmap"]
    mock_prepare.return_value = "Hook Prompt"
    mock_run.return_value = (0, "stdout", "")

    # Task must be IN_PROGRESS for finalization to apply
    tasks.update_task(
        hooks_env, "12345678", status=tasks.TaskStatus.IN_PROGRESS
    )

    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=True,
        final_status=tasks.TaskStatus.COMPLETED,
    )

    assert mock_run.called
    data = tasks.load_tasks(hooks_env)
    assert data.tasks[0].status == tasks.TaskStatus.COMPLETED


@mock.patch("lemming.runner.run_with_heartbeat")
@mock.patch("lemming.prompts.prepare_hook_prompt")
def test_run_hooks_returns_exit_codes(mock_prepare, mock_run, hooks_env):
    mock_prepare.return_value = "Hook Prompt"
    mock_run.return_value = (1, "", "runner crashed")

    tasks.update_task(
        hooks_env, "12345678", status=tasks.TaskStatus.IN_PROGRESS
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
        final_status=tasks.TaskStatus.COMPLETED,
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
        final_status=tasks.TaskStatus.COMPLETED,
    )

    assert not mock_run.called
    data = tasks.load_tasks(hooks_env)
    assert data.tasks[0].status == tasks.TaskStatus.COMPLETED


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_failure_filters_hooks(mock_run, mock_prepare, hooks_env):
    mock_run.return_value = (0, "stdout", "")
    mock_prepare.return_value = "Mock Prompt"

    # Task must be IN_PROGRESS for finalization to apply
    tasks.update_task(
        hooks_env, "12345678", status=tasks.TaskStatus.IN_PROGRESS
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
        final_status=tasks.TaskStatus.FAILED,
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

    tasks.update_task(
        hooks_env, "12345678", status=tasks.TaskStatus.IN_PROGRESS
    )

    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=True,
        final_status=tasks.TaskStatus.FAILED,
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
    tasks.update_task(
        hooks_env, "12345678", status=tasks.TaskStatus.IN_PROGRESS
    )

    # Simulate the hook resetting the task during execution
    def hook_resets_task(*args, **kwargs):
        tasks.reset_task(hooks_env, "12345678")
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
        final_status=tasks.TaskStatus.FAILED,
    )

    # Task should remain PENDING (healed), not FAILED
    data = tasks.load_tasks(hooks_env)
    assert data.tasks[0].status == tasks.TaskStatus.PENDING
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
    tasks.update_task(
        hooks_env, "12345678", status=tasks.TaskStatus.IN_PROGRESS
    )
    tasks.claim_task(hooks_env, "12345678", pid=os.getpid())
    tasks.update_task(hooks_env, "12345678", status=tasks.TaskStatus.COMPLETED)

    run_hooks(
        hooks_env,
        "12345678",
        "agy",
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=False,
        hooks=["roadmap"],
        final_status=tasks.TaskStatus.COMPLETED,
    )

    # The task should be retried from scratch, keeping its attempt count.
    data = tasks.load_tasks(hooks_env)
    task = data.tasks[0]
    assert task.status == tasks.TaskStatus.PENDING
    assert task.requested_status is None
    assert task.attempts == 1
    assert any("hook" in p.lower() for p in task.progress)


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_failure_finalization_ignores_hook_errors(
    mock_run, mock_prepare, hooks_env
):
    """Failed hooks must not revert a task that is being marked FAILED."""
    mock_prepare.return_value = "Mock Prompt"
    mock_run.return_value = (1, "", "")

    tasks.update_task(
        hooks_env, "12345678", status=tasks.TaskStatus.IN_PROGRESS
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
        final_status=tasks.TaskStatus.FAILED,
    )

    data = tasks.load_tasks(hooks_env)
    assert data.tasks[0].status == tasks.TaskStatus.FAILED


@mock.patch("lemming.prompts.prepare_hook_prompt")
@mock.patch("lemming.runner.run_with_heartbeat")
def test_run_hooks_reloads_tasks(mock_run, mock_prepare, hooks_env):
    # Create a real Roadmap object to return
    real_data = tasks.load_tasks(hooks_env)
    mock_run.return_value = (0, "stdout", "")
    mock_prepare.return_value = "Mock Prompt"

    with mock.patch(
        "lemming.orchestrator.tasks.load_tasks", return_value=real_data
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

        # 1 initial load + 2 hook loads = 3
        assert mock_load.call_count == 3
