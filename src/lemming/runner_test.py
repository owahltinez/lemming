import pathlib
import signal
import subprocess
import sys
import time
import unittest.mock

from lemming import models, paths, persistence, runner, tasks


def test_build_runner_command_agy():
    cmd = runner.build_runner_command("agy", "my prompt", yolo=True)
    assert "--dangerously-skip-permissions" in cmd
    assert "--prompt" in cmd
    assert "my prompt" in cmd


def test_build_runner_command_agy_adds_project_workspace():
    working_dir = pathlib.Path("/tmp/project with spaces")
    cmd = runner.build_runner_command(
        "agy",
        "my prompt",
        yolo=True,
        working_dir=working_dir,
    )

    add_dir_index = cmd.index("--add-dir")
    assert cmd[add_dir_index + 1] == str(working_dir)


def test_build_runner_command_agy_without_defaults_skips_project_workspace():
    cmd = runner.build_runner_command(
        "agy",
        "my prompt",
        yolo=True,
        no_defaults=True,
        working_dir=pathlib.Path("/tmp/project"),
    )

    assert "--add-dir" not in cmd


def test_build_runner_command_agy_streams_json_events():
    # agy print mode buffers the response until the end, so stream-json is
    # used to surface agent messages and tool calls live in the task log.
    cmd = runner.build_runner_command("agy", "my prompt", yolo=True)
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--log-file" not in cmd


def test_build_runner_command_agy_print_timeout_matches_time_limit():
    cmd = runner.build_runner_command(
        "agy", "my prompt", yolo=True, time_limit=45
    )
    assert cmd[cmd.index("--print-timeout") + 1] == "45m"


def test_build_runner_command_agy_print_timeout_without_time_limit():
    # agy defaults --print-timeout to 5m, so an explicit large value is
    # required even when the task has no time limit.
    cmd = runner.build_runner_command(
        "agy", "my prompt", yolo=True, time_limit=0
    )
    assert cmd[cmd.index("--print-timeout") + 1] == "24h"


def test_build_runner_command_time_limit_ignored_by_other_runners():
    cmd = runner.build_runner_command(
        "claude",
        "my prompt",
        yolo=True,
        time_limit=45,
        working_dir=pathlib.Path("/tmp/project"),
    )
    assert "--print-timeout" not in cmd
    assert "--add-dir" not in cmd
    assert "--log-file" not in cmd


def test_build_runner_command_aider():
    cmd = runner.build_runner_command("aider", "my prompt", yolo=True)
    assert "--yes" in cmd
    assert "--message" in cmd


def test_build_runner_command_codex():
    cmd = runner.build_runner_command("codex", "my prompt", yolo=True)
    assert cmd == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
        "my prompt",
    ]


def test_build_runner_command_codex_without_yolo():
    cmd = runner.build_runner_command("codex", "my prompt", yolo=False)
    assert cmd == ["codex", "exec", "--json", "my prompt"]


def test_build_runner_command_codex_without_defaults_still_uses_exec():
    cmd = runner.build_runner_command(
        "codex", "my prompt", yolo=True, no_defaults=True
    )
    assert cmd == ["codex", "exec", "my prompt"]


def test_build_runner_command_codex_does_not_duplicate_explicit_exec():
    cmd = runner.build_runner_command(
        "codex exec --ephemeral", "my prompt", yolo=False
    )
    assert cmd == ["codex", "exec", "--json", "--ephemeral", "my prompt"]


def test_build_runner_command_with_flags_in_name():
    cmd = runner.build_runner_command(
        "claude-corp -- --output-format=stream-json", "my prompt", yolo=True
    )
    assert cmd[0] == "claude-corp"
    assert "--" in cmd
    assert "--output-format=stream-json" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--print" in cmd
    assert "my prompt" in cmd


def test_build_runner_command_with_quoted_flags_in_name():
    cmd = runner.build_runner_command(
        'my-runner --model "gpt 4"', "my prompt", yolo=True, no_defaults=True
    )
    assert cmd[0] == "my-runner"
    assert "--model" in cmd
    assert "gpt 4" in cmd


def test_build_runner_command_template_basic():
    cmd = runner.build_runner_command(
        "my-tool --input={{prompt}} --json", "hello world", yolo=True
    )
    assert cmd == ["my-tool", "--input=hello world", "--json"]


def test_build_runner_command_template_standalone_placeholder():
    cmd = runner.build_runner_command(
        "my-tool --flag {{prompt}}", "hello world", yolo=True
    )
    assert cmd == ["my-tool", "--flag", "hello world"]


def test_build_runner_command_template_with_runner_args():
    cmd = runner.build_runner_command(
        "my-tool {{prompt}}", "hello", yolo=True, runner_args=("--extra",)
    )
    assert cmd == ["my-tool", "hello", "--extra"]


def test_per_task_flag_overrides_global_passthrough():
    """A per-task runner flag must defeat the same loop-wide flag."""
    cmd = runner.build_runner_command(
        "claude --model per-task",
        "prompt",
        yolo=True,
        runner_args=("--model", "global"),
    )

    assert cmd.count("--model") == 1
    assert "global" not in cmd
    assert cmd[cmd.index("--model") + 1] == "per-task"


def test_per_task_flag_overrides_global_equals_form():
    """The --flag=value spelling is dropped as a single token."""
    cmd = runner.build_runner_command(
        "claude --model=per-task",
        "prompt",
        yolo=True,
        runner_args=("--model=global",),
    )

    assert "--model=global" not in cmd
    assert "--model=per-task" in cmd


def test_global_passthrough_kept_when_no_conflict():
    """Unrelated loop-wide args still reach the runner."""
    cmd = runner.build_runner_command(
        "claude --model per-task",
        "prompt",
        yolo=True,
        runner_args=("--fallback-model", "other"),
    )

    assert "--fallback-model" in cmd
    assert cmd[cmd.index("--fallback-model") + 1] == "other"
    assert cmd[cmd.index("--model") + 1] == "per-task"


def test_conflicting_boolean_flag_does_not_consume_next_flag():
    """Dropping a valueless flag must not swallow the following argument."""
    cmd = runner.build_runner_command(
        "claude --verbose",
        "prompt",
        yolo=True,
        runner_args=("--verbose", "--fallback-model", "other"),
    )

    assert "--fallback-model" in cmd
    assert cmd[cmd.index("--fallback-model") + 1] == "other"


def test_model_is_appended_for_known_runners():
    """A configured model reaches the runner as --model."""
    cmd = runner.build_runner_command(
        "agy", "prompt", yolo=True, model="gemini-3.6-flash-high"
    )

    assert cmd[cmd.index("--model") + 1] == "gemini-3.6-flash-high"


def test_model_defers_to_explicit_runner_string_flag():
    """An explicit --model in the runner string beats the model field."""
    cmd = runner.build_runner_command(
        "agy --model explicit", "prompt", yolo=True, model="configured"
    )

    assert cmd.count("--model") == 1
    assert cmd[cmd.index("--model") + 1] == "explicit"


def test_model_overrides_global_passthrough():
    """The model field beats a loop-wide --model, mirroring per-task args."""
    cmd = runner.build_runner_command(
        "codex",
        "prompt",
        yolo=True,
        runner_args=("--model", "global"),
        model="per-task",
    )

    assert cmd.count("--model") == 1
    assert cmd[cmd.index("--model") + 1] == "per-task"


def test_model_skipped_in_template_mode():
    """Template mode hands full command control to the user."""
    cmd = runner.build_runner_command(
        "my-tool {{prompt}}", "hello", yolo=True, model="ignored"
    )

    assert cmd == ["my-tool", "hello"]


def test_describe_command_elides_the_prompt():
    """Provenance records the command without the full prompt text."""
    cmd = runner.build_runner_command(
        "agy", "a very long prompt", yolo=True, model="some-model"
    )

    described = runner.describe_command(cmd, "a very long prompt")

    assert "a very long prompt" not in described
    assert "--model some-model" in described
    assert described.startswith("agy ")


def test_build_runner_command_template_ignores_defaults():
    # Even though runner starts with "agy", template mode should not
    # inject --dangerously-skip-permissions etc.
    cmd = runner.build_runner_command(
        "agy --custom {{prompt}}",
        "do stuff",
        yolo=True,
        working_dir=pathlib.Path("/tmp/project"),
    )
    assert "--dangerously-skip-permissions" not in cmd
    assert "--add-dir" not in cmd
    assert cmd == ["agy", "--custom", "do stuff"]


def test_build_runner_command_template_prompt_in_flag_value():
    cmd = runner.build_runner_command(
        "my-tool --msg={{prompt}} --verbose", "hi there", yolo=True
    )
    assert cmd == ["my-tool", "--msg=hi there", "--verbose"]


def test_pretty_quote():
    # Test fallback to shlex
    assert runner._pretty_quote("simple") == "simple"
    assert runner._pretty_quote("has space") == "'has space'"

    # Test readable double quotes for single quotes
    assert (
        runner._pretty_quote("has 'single' quotes") == "\"has 'single' quotes\""
    )
    assert runner._pretty_quote("You are 'Lemming'") == "\"You are 'Lemming'\""

    # Test string with double quotes (should fall back to single quotes)
    assert (
        runner._pretty_quote('has "double" quotes') == "'has \"double\" quotes'"
    )

    # Test escaping specials inside double quotes
    assert (
        runner._pretty_quote("has 'single' and \"double\" quotes")
        == '"has \'single\' and \\"double\\" quotes"'
    )

    # Test exclamation mark fallback
    assert runner._pretty_quote("Hello!") == "'Hello!'"

    assert runner._pretty_quote("has 'single' and !") == (
        "'has '\"'\"'single'\"'\"' and !'"
    )

    # Test idempotency (should NOT compound quotes)
    q = runner._pretty_quote("it's!")
    assert q == "'it'\"'\"'s!'"
    qq = runner._pretty_quote(q)
    assert qq == q
    qqq = runner._pretty_quote(qq)
    assert qqq == q

    # Test idempotent single quotes (should NOT compound to multiple escapes)
    q_s = runner._pretty_quote("it's")
    assert q_s == '"it\'s"'
    qq_s = runner._pretty_quote(q_s)
    assert qq_s == q_s

    # Test complex shell-quoted strings
    already_quoted = "'path with space' and \"double'quotes\""
    # This is NOT a single shell word, so it won't be unquoted.
    # But it will be double-quoted correctly.
    q_complex = runner._pretty_quote(already_quoted)
    assert q_complex.startswith('"')
    assert q_complex.endswith('"')
    assert '\\"double\'quotes\\"' in q_complex


def test_shlex_join_pretty():
    cmd = [
        "example-cli",
        "--dangerously-skip-permissions",
        "--print",
        "You are 'Lemming'",
    ]
    joined = runner._shlex_join_pretty(cmd)
    assert (
        joined == "example-cli --dangerously-skip-permissions --print"
        " \"You are 'Lemming'\""
    )

    # Test truncation
    long_arg = "a" * 300
    joined_truncated = runner._shlex_join_pretty(["cli", long_arg], max_len=100)
    assert "a" * 100 in joined_truncated
    assert "... [truncated]" in joined_truncated
    assert len(joined_truncated) < 150


def test_run_with_heartbeat_truncation_only_affects_log(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task_id = "test_task"
    tasks.save_tasks(
        tasks_file,
        tasks.Roadmap(tasks=[tasks.Task(id=task_id, description="test")]),
    )
    tasks.mark_task_in_progress(tasks_file, task_id)
    log_file = paths.get_log_file(tasks_file, task_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Use a long command that would be truncated in logs
    long_arg = "a" * 300
    cmd = ["echo", long_arg]

    mock_process = unittest.mock.MagicMock()
    mock_process.pid = 12345
    mock_process.returncode = 0
    mock_process.stdout = None
    mock_process.poll.return_value = 0

    with unittest.mock.patch(
        "subprocess.Popen", return_value=mock_process
    ) as mock_popen:
        runner.run_with_heartbeat(cmd, tasks_file, task_id, verbose=False)

        # Verify Popen received the original untruncated cmd
        mock_popen.assert_called_once()
        called_cmd = mock_popen.call_args[0][0]
        assert called_cmd == cmd
        assert len(called_cmd[1]) == 300

    # Verify log file contains the truncated command
    content = log_file.read_text()
    assert "Command: echo " in content
    assert "a" * 200 in content
    assert "... [truncated]" in content
    assert "a" * 201 not in content


def test_run_with_heartbeat_log_header(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task_id = "test_task"
    task_id_2 = "test_task_2"
    tasks.save_tasks(
        tasks_file,
        tasks.Roadmap(
            tasks=[
                tasks.Task(id=task_id, description="test"),
                tasks.Task(id=task_id_2, description="test 2"),
            ]
        ),
    )
    tasks.mark_task_in_progress(tasks_file, task_id)
    tasks.mark_task_in_progress(tasks_file, task_id_2)
    log_file = paths.get_log_file(tasks_file, task_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Use a command that exits quickly
    cmd = ["true"]

    # 1. Run with a header
    runner.run_with_heartbeat(
        cmd, tasks_file, task_id, verbose=False, header="Hook: roadmap"
    )

    content = log_file.read_text()
    assert "--- Attempt started at" in content
    assert "HOOK: ROADMAP started at" in content
    assert "=" * 80 in content

    # 2. Run without a header (it should still have the attempt marker)
    log_file_2 = paths.get_log_file(tasks_file, task_id_2)
    runner.run_with_heartbeat(
        cmd, tasks_file, task_id_2, verbose=False, header=None
    )

    content_2 = log_file_2.read_text()
    assert "--- Attempt started at" in content_2
    assert "started at" not in content_2.replace("Attempt started at", "")
    assert "=" * 80 not in content_2


def test_run_with_heartbeat_records_runner_and_hook_times(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task_id = "timed_task"
    tasks.save_tasks(
        tasks_file,
        tasks.Roadmap(tasks=[tasks.Task(id=task_id, description="timed task")]),
    )
    tasks.mark_task_in_progress(tasks_file, task_id)

    runner.run_with_heartbeat(["true"], tasks_file, task_id, verbose=False)
    runner.run_with_heartbeat(
        ["true"],
        tasks_file,
        task_id,
        verbose=False,
        header="Hook: testing",
    )

    task = tasks.load_tasks(tasks_file).tasks[0]
    assert task.execution_times is not None
    assert task.execution_times["runner"] > 0
    assert task.execution_times["hook:testing"] > 0
    assert task.active_execution_component is None
    assert task.active_execution_started_at is None


def test_run_with_heartbeat_interruption_cleanup(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task_id = "test_task"

    # 1. Setup a dummy Roadmap
    roadmap = tasks.Roadmap(tasks=[tasks.Task(id=task_id, description="test")])
    tasks.save_tasks(tasks_file, roadmap)
    tasks.mark_task_in_progress(tasks_file, task_id)

    # 2. Mock subprocess.Popen and related functions
    mock_process = unittest.mock.MagicMock()
    mock_process.pid = 12345
    mock_process.stdout = None
    # We want process.wait() to raise a KeyboardInterrupt (BaseException)
    mock_process.wait.side_effect = KeyboardInterrupt()

    with (
        unittest.mock.patch("subprocess.Popen", return_value=mock_process),
        unittest.mock.patch("os.killpg") as mock_killpg,
        unittest.mock.patch("os.getpgid", return_value=54321),
    ):
        # 3. Call run_with_heartbeat and expect it to re-raise KeyboardInterrupt
        try:
            runner.run_with_heartbeat(
                ["long-running-cmd"], tasks_file, task_id, verbose=False
            )
        except KeyboardInterrupt:
            pass
        else:
            assert False, "KeyboardInterrupt was not raised"

        # 4. Verify cleanup was attempted
        mock_killpg.assert_called_once_with(54321, signal.SIGTERM)


def test_run_with_heartbeat_kills_runner_if_task_was_cancelled(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task_id = "cancelled"
    tasks.save_tasks(
        tasks_file,
        tasks.Roadmap(
            tasks=[
                tasks.Task(
                    id=task_id,
                    description="cancelled task",
                    status=tasks.TaskStatus.CANCELLED,
                )
            ]
        ),
    )

    started = time.monotonic()
    returncode, _, _ = runner.run_with_heartbeat(
        ["sleep", "60"], tasks_file, task_id, verbose=False
    )

    assert returncode == -signal.SIGTERM
    assert time.monotonic() - started < 10


def test_returncode_timeout_constant():
    assert runner.RETURNCODE_TIMEOUT == -14


def test_kill_process_tree_killpg():
    """Verifies _kill_process_tree uses killpg first."""
    process = unittest.mock.MagicMock(spec=subprocess.Popen)
    process.pid = 12345

    with (
        unittest.mock.patch("os.killpg") as mock_killpg,
        unittest.mock.patch("os.getpgid", return_value=54321),
    ):
        runner._kill_process_tree(process)
        mock_killpg.assert_called_once_with(54321, signal.SIGTERM)
        process.kill.assert_not_called()


def test_kill_process_tree_fallback():
    """Verifies _kill_process_tree falls back to process.kill on OSError."""
    process = unittest.mock.MagicMock(spec=subprocess.Popen)
    process.pid = 12345

    with (
        unittest.mock.patch("os.killpg", side_effect=OSError),
        unittest.mock.patch("os.getpgid", return_value=54321),
    ):
        runner._kill_process_tree(process)
        process.kill.assert_called_once()


def test_run_with_heartbeat_timeout(tmp_path):
    """Verifies run_with_heartbeat kills and records progress on timeout."""
    tasks_file = tmp_path / "tasks.yml"
    task_id = "timeout_task"

    # Setup a task so heartbeat updates work
    roadmap = tasks.Roadmap(
        tasks=[tasks.Task(id=task_id, description="test timeout")]
    )
    tasks.save_tasks(tasks_file, roadmap)
    tasks.mark_task_in_progress(tasks_file, task_id)

    # Use a 1-minute time limit. Mock time.monotonic to simulate elapsed
    # time so the heartbeat loop detects the timeout immediately without
    # waiting 60 real seconds.
    real_monotonic = time.monotonic
    call_count = 0

    def fast_monotonic():
        nonlocal call_count
        call_count += 1
        # After the first call (start_time), jump 2 minutes ahead
        if call_count > 1:
            return real_monotonic() + 120
        return real_monotonic()

    with unittest.mock.patch("time.monotonic", side_effect=fast_monotonic):
        returncode, stdout, stderr = runner.run_with_heartbeat(
            ["sleep", "60"],
            tasks_file,
            task_id,
            verbose=False,
            time_limit=1,
        )

    assert returncode == runner.RETURNCODE_TIMEOUT

    # Verify the timeout progress was recorded
    data = tasks.load_tasks(tasks_file)
    task = next(t for t in data.tasks if t.id == task_id)
    assert any("time limit" in o for o in task.progress)


def test_run_with_heartbeat_no_timeout(tmp_path):
    """Verifies that time_limit=0 does not enforce any timeout."""
    tasks_file = tmp_path / "tasks.yml"
    task_id = "no_timeout"

    roadmap = tasks.Roadmap(
        tasks=[tasks.Task(id=task_id, description="test no timeout")]
    )
    tasks.save_tasks(tasks_file, roadmap)
    tasks.mark_task_in_progress(tasks_file, task_id)

    returncode, _, _ = runner.run_with_heartbeat(
        ["true"],
        tasks_file,
        task_id,
        verbose=False,
        time_limit=0,
    )

    assert returncode == 0


def test_kill_process_tree_escalates_to_sigkill():
    """Verifies SIGKILL escalation when SIGTERM does not stop the process."""
    process = unittest.mock.MagicMock(spec=subprocess.Popen)
    process.pid = 12345
    process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=1)

    with (
        unittest.mock.patch("os.killpg") as mock_killpg,
        unittest.mock.patch("os.getpgid", return_value=54321),
    ):
        runner._kill_process_tree(process)

    assert mock_killpg.call_args_list == [
        unittest.mock.call(54321, signal.SIGTERM),
        unittest.mock.call(54321, signal.SIGKILL),
    ]


def test_run_with_heartbeat_timeout_kills_sigterm_immune_runner(tmp_path):
    """Verifies a runner that ignores SIGTERM is still killed on timeout."""
    tasks_file = tmp_path / "tasks.yml"
    task_id = "sigterm_immune"

    roadmap = tasks.Roadmap(
        tasks=[tasks.Task(id=task_id, description="test escalation")]
    )
    tasks.save_tasks(tasks_file, roadmap)
    tasks.mark_task_in_progress(tasks_file, task_id)

    # The runner ignores SIGTERM and signals readiness via a sentinel file.
    sentinel = tmp_path / "ready"
    child_code = (
        "import pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(sentinel)!r}).touch()\n"
        "time.sleep(300)\n"
    )

    # Jump the clock past the limit only once the child has installed its
    # SIGTERM handler, so the kill cannot land before setup completes.
    real_monotonic = time.monotonic

    def fast_monotonic():
        return real_monotonic() + (120 if sentinel.exists() else 0)

    start = real_monotonic()
    with (
        unittest.mock.patch("time.monotonic", side_effect=fast_monotonic),
        unittest.mock.patch.object(tasks, "STALE_THRESHOLD", 2),
        unittest.mock.patch.object(runner, "KILL_GRACE_SECONDS", 1),
    ):
        returncode, _, _ = runner.run_with_heartbeat(
            [sys.executable, "-c", child_code],
            tasks_file,
            task_id,
            verbose=False,
            time_limit=1,
        )

    assert returncode == runner.RETURNCODE_TIMEOUT
    assert real_monotonic() - start < 30


def test_run_with_heartbeat_returns_when_grandchild_holds_stdout(tmp_path):
    """Verifies return when a detached grandchild keeps the pipe open.

    A process that starts its own session escapes the group kill and
    inherits the stdout pipe; reading until EOF would block until it
    exits, long after the runner itself is gone.
    """
    tasks_file = tmp_path / "tasks.yml"
    task_id = "detached_grandchild"

    roadmap = tasks.Roadmap(
        tasks=[tasks.Task(id=task_id, description="test pipe hold")]
    )
    tasks.save_tasks(tasks_file, roadmap)
    tasks.mark_task_in_progress(tasks_file, task_id)

    child_code = (
        "import subprocess, sys\n"
        "subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(30)'],\n"
        "    start_new_session=True,\n"
        ")\n"
        "print('runner done', flush=True)\n"
    )

    start = time.monotonic()
    with unittest.mock.patch.object(runner, "OUTPUT_DRAIN_SECONDS", 1):
        returncode, stdout, _ = runner.run_with_heartbeat(
            [sys.executable, "-c", child_code],
            tasks_file,
            task_id,
            verbose=False,
            time_limit=0,
        )

    assert returncode == 0
    assert "runner done" in stdout
    assert time.monotonic() - start < 10


def _run_interrupted_at(tmp_path, patched: str) -> subprocess.Popen:
    """Interrupts a runner at a given bookkeeping call, returning the child.

    The window between Popen and process.wait() covers heartbeat bookkeeping,
    so a stop request landing anywhere in it must not leak the child.
    """
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(tasks=[models.Task(id="t1", description="d")]),
    )
    tasks.mark_task_in_progress(tasks_file, "t1")

    spawned: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def capturing_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    with (
        unittest.mock.patch("subprocess.Popen", side_effect=capturing_popen),
        unittest.mock.patch(patched, side_effect=KeyboardInterrupt),
    ):
        try:
            runner.run_with_heartbeat(
                ["sleep", "60"], tasks_file, "t1", verbose=False
            )
        except KeyboardInterrupt:
            pass

    assert spawned, "The runner process was never started"
    return spawned[0]


def test_interrupt_during_heartbeat_kills_the_child(tmp_path):
    """A stop landing on the heartbeat write must not leak the runner."""
    child = _run_interrupted_at(tmp_path, "lemming.tasks.update_heartbeat")

    child.wait(timeout=10)


def test_interrupt_during_marking_kills_the_child(tmp_path):
    """The earliest bookkeeping call is inside the protected window too."""
    child = _run_interrupted_at(
        tmp_path, "lemming.tasks.mark_execution_started"
    )

    child.wait(timeout=10)


def test_extract_error_message_from_error_event():
    """The runner's own diagnosis beats a generic failure notice."""
    output = (
        '{"type":"turn.started"}\n'
        '{"type":"error","message":"You\'ve hit your usage limit."}\n'
    )

    assert (
        runner.extract_error_message(output) == "You've hit your usage limit."
    )


def test_extract_error_message_unwraps_nested_json():
    """Some runners wrap the human message in another JSON payload."""
    inner = (
        '{\\"type\\":\\"error\\",\\"status\\":400,\\"error\\":'
        '{\\"type\\":\\"invalid_request_error\\",\\"message\\":'
        "\\\"The 'sonnet' model is not supported when using Codex.\\\"}}"
    )
    output = '{"type":"error","message":"' + inner + '"}\n'

    assert (
        runner.extract_error_message(output)
        == "The 'sonnet' model is not supported when using Codex."
    )


def test_extract_error_message_from_turn_failed():
    output = '{"type":"turn.failed","error":{"message":"quota exhausted"}}\n'

    assert runner.extract_error_message(output) == "quota exhausted"


def test_extract_error_message_ignores_tool_failures():
    """A failed shell command inside the agent is not the runner failing."""
    output = (
        '{"type":"user","is_error":true,"tool_use_result":"grep: no match"}\n'
    )

    assert runner.extract_error_message(output) is None


def test_extract_error_message_returns_none_without_an_error():
    assert runner.extract_error_message('{"type":"turn.completed"}\n') is None
    assert runner.extract_error_message("") is None
    assert runner.extract_error_message("not json at all\n") is None


def test_extract_error_message_prefers_the_last_error():
    output = (
        '{"type":"error","message":"first"}\n'
        '{"type":"error","message":"second"}\n'
    )

    assert runner.extract_error_message(output) == "second"


def test_extract_error_message_is_bounded():
    output = '{"type":"error","message":"' + "x" * 900 + '"}\n'

    message = runner.extract_error_message(output)

    assert len(message) <= 200
