import signal
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


def test_run_with_heartbeat_log_header(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task_id = "test_task"
    log_file = paths.get_log_file(tasks_file, task_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Use a command that exits quickly
    cmd = ["true"]

    # Run with a header
    runner.run_with_heartbeat(
        cmd, tasks_file, task_id, verbose=False, header="Hook: roadmap"
    )

    content = log_file.read_text()
    assert "HOOK: ROADMAP started at" in content
    assert "HOOK: HOOK: ROADMAP" not in content
    assert "=" * 80 in content


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
