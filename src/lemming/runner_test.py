import signal
import subprocess
import time
import unittest.mock

from lemming import runner
from lemming import tasks
from lemming import paths


def test_build_runner_command_gemini():
    cmd = runner.build_runner_command("gemini", "my prompt", yolo=True)
    assert "--yolo" in cmd
    assert "--prompt" in cmd
    assert "my prompt" in cmd


def test_build_runner_command_aider():
    cmd = runner.build_runner_command("aider", "my prompt", yolo=True)
    assert "--yes" in cmd
    assert "--message" in cmd


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


def test_build_runner_command_template_ignores_defaults():
    # Even though runner starts with "gemini", template mode should not
    # inject --yolo etc.
    cmd = runner.build_runner_command(
        "gemini --custom {{prompt}}", "do stuff", yolo=True
    )
    assert "--yolo" not in cmd
    assert "--no-sandbox" not in cmd
    assert cmd == ["gemini", "--custom", "do stuff"]


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
    assert runner._pretty_quote("has 'single' quotes") == "\"has 'single' quotes\""
    assert runner._pretty_quote("You are 'Lemming'") == "\"You are 'Lemming'\""

    # Test string with double quotes (should fall back to single quotes)
    assert runner._pretty_quote('has "double" quotes') == "'has \"double\" quotes'"

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
        joined
        == "example-cli --dangerously-skip-permissions --print \"You are 'Lemming'\""
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
    log_file = paths.get_log_file(tasks_file, task_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Use a long command that would be truncated in logs
    long_arg = "a" * 300
    cmd = ["echo", long_arg]

    mock_process = unittest.mock.MagicMock()
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
    task_id_2 = "test_task_2"
    log_file_2 = paths.get_log_file(tasks_file, task_id_2)
    runner.run_with_heartbeat(cmd, tasks_file, task_id_2, verbose=False, header=None)

    content_2 = log_file_2.read_text()
    assert "--- Attempt started at" in content_2
    assert "started at" not in content_2.replace("Attempt started at", "")
    assert "=" * 80 not in content_2


def test_run_with_heartbeat_interruption_cleanup(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task_id = "test_task"

    # 1. Setup a dummy Roadmap
    roadmap = tasks.Roadmap(tasks=[tasks.Task(id=task_id, description="test")])
    tasks.save_tasks(tasks_file, roadmap)

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
    """Verifies that run_with_heartbeat kills the process and records an outcome on timeout."""
    tasks_file = tmp_path / "tasks.yml"
    task_id = "timeout_task"

    # Setup a task so heartbeat updates work
    roadmap = tasks.Roadmap(tasks=[tasks.Task(id=task_id, description="test timeout")])
    tasks.save_tasks(tasks_file, roadmap)
    tasks.mark_task_in_progress(tasks_file, task_id)

    # Use a 1-minute time limit. Mock time.monotonic to simulate elapsed time so the
    # heartbeat loop detects the timeout immediately without waiting 60 real seconds.
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

    # Verify the timeout outcome was recorded
    data = tasks.load_tasks(tasks_file)
    task = next(t for t in data.tasks if t.id == task_id)
    assert any("time limit" in o for o in task.outcomes)


def test_run_with_heartbeat_no_timeout(tmp_path):
    """Verifies that time_limit=0 does not enforce any timeout."""
    tasks_file = tmp_path / "tasks.yml"
    task_id = "no_timeout"

    roadmap = tasks.Roadmap(
        tasks=[tasks.Task(id=task_id, description="test no timeout")]
    )
    tasks.save_tasks(tasks_file, roadmap)

    returncode, _, _ = runner.run_with_heartbeat(
        ["true"],
        tasks_file,
        task_id,
        verbose=False,
        time_limit=0,
    )

    assert returncode == 0
