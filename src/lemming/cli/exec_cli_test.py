"""Tests for the one-shot exec command."""

import json
from unittest import mock

import pytest
from click.testing import CliRunner

from lemming import paths, tasks
from lemming.cli import cli


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Runs each test from an empty project with its own Lemming home.

    Runs that fail keep their state directory on purpose, so a shared home
    would let one test's leftovers be counted by the next.
    """
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    return project


def _finish(message, returncode=0, status=tasks.TaskStatus.COMPLETED):
    """Builds a run_with_heartbeat fake that finishes the claimed task.

    The real runner reaches a terminal state by having the agent invoke the
    CLI, so a fake must both write its event stream to the log and settle
    the task the way a completed run would.
    """
    output = json.dumps({"type": "result", "result": message}) + "\n"

    def fake(cmd, tasks_file, task_id, *args, **kwargs):
        log_file = paths.get_log_file(tasks_file, task_id)
        log_file.write_text(f"Command: {cmd[0]}\n{output}", encoding="utf-8")
        tasks.update_task(tasks_file, task_id, status=status, force=True)
        return returncode, output, ""

    return fake


def _exec_dirs():
    """Returns the ephemeral state directories currently on disk."""
    return sorted(paths.get_lemming_home().glob("exec-*"))


def test_exec_prints_the_final_message_to_stdout(workspace):
    """The agent's closing message is the command's return value."""
    with mock.patch(
        "lemming.runner.run_with_heartbeat", _finish("Fixed the flaky test.")
    ):
        result = CliRunner().invoke(cli, ["exec", "Fix the flaky test"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "Fixed the flaky test."


def test_exec_keeps_progress_chatter_off_stdout(workspace):
    """Stdout is the interface, so loop output belongs on stderr."""
    with mock.patch("lemming.runner.run_with_heartbeat", _finish("Done.")):
        result = CliRunner().invoke(cli, ["exec", "Fix the flaky test"])

    assert result.stdout.strip() == "Done."
    assert "Attempt" in result.stderr


def test_exec_removes_its_state_directory_on_success(workspace):
    """A one-shot leaves nothing behind when it worked."""
    with mock.patch("lemming.runner.run_with_heartbeat", _finish("Done.")):
        CliRunner().invoke(cli, ["exec", "Fix the flaky test"])

    assert _exec_dirs() == []


def test_exec_keeps_its_state_directory_on_failure(workspace):
    """A failed run must stay debuggable through its log."""
    fake = _finish("Could not fix it.", status=tasks.TaskStatus.FAILED)
    with mock.patch("lemming.runner.run_with_heartbeat", fake):
        result = CliRunner().invoke(cli, ["exec", "Fix the flaky test"])

    assert result.exit_code != 0
    assert len(_exec_dirs()) == 1
    assert "exec-" in result.stderr


def test_exec_keeps_its_state_directory_when_asked(workspace):
    """--keep retains the log of a run that succeeded."""
    with mock.patch("lemming.runner.run_with_heartbeat", _finish("Done.")):
        result = CliRunner().invoke(
            cli, ["exec", "Fix the flaky test", "--keep"]
        )

    assert result.exit_code == 0
    assert len(_exec_dirs()) == 1


def test_exec_accepts_a_prompt_over_the_description_limit(workspace):
    """A delegating agent's context exceeds the roadmap description cap."""
    prompt = "Fix the flaky test. " + "Context line. " * 500
    assert len(prompt) > 2_000

    with mock.patch("lemming.runner.run_with_heartbeat", _finish("Done.")):
        result = CliRunner().invoke(cli, ["exec", prompt])

    assert result.exit_code == 0, result.stderr


def test_exec_reads_the_prompt_from_stdin(workspace):
    """Piping the prompt avoids shell quoting for long handoffs."""
    with mock.patch("lemming.runner.run_with_heartbeat", _finish("Done.")):
        result = CliRunner().invoke(
            cli, ["exec", "-f", "-"], input="Fix the flaky test\n"
        )

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "Done."


def test_exec_requires_a_prompt(workspace):
    """With no description and no reviews there is nothing to run."""
    result = CliRunner().invoke(cli, ["exec"])

    assert result.exit_code != 0


def test_exec_does_not_touch_the_project_roadmap(workspace):
    """A stale roadmap must neither be read nor written by a one-shot."""
    project_tasks = workspace / "tasks.yml"
    tasks.save_tasks(
        project_tasks,
        tasks.Roadmap(
            goal="An abandoned goal from months ago",
            tasks=[tasks.Task(id="old1", description="Old task")],
        ),
    )
    before = project_tasks.read_text(encoding="utf-8")

    with mock.patch("lemming.runner.run_with_heartbeat", _finish("Done.")):
        result = CliRunner().invoke(cli, ["exec", "Fix the flaky test"])

    assert result.exit_code == 0, result.stderr
    assert project_tasks.read_text(encoding="utf-8") == before


def test_exec_runs_no_hooks_by_default(workspace):
    """One-shot means one agent run, not a review pipeline."""
    headers = []

    def record(cmd, tasks_file, task_id, *args, **kwargs):
        headers.append(kwargs.get("header"))
        return _finish("Done.")(cmd, tasks_file, task_id, *args, **kwargs)

    with mock.patch("lemming.runner.run_with_heartbeat", record):
        CliRunner().invoke(cli, ["exec", "Fix the flaky test"])

    assert headers == ["Task Runner"]


def test_exec_passes_the_requested_runner_and_model(workspace):
    """The abstraction's whole point is choosing another agent CLI."""
    commands = []

    def record(cmd, tasks_file, task_id, *args, **kwargs):
        commands.append(cmd)
        return _finish("Done.")(cmd, tasks_file, task_id, *args, **kwargs)

    with mock.patch("lemming.runner.run_with_heartbeat", record):
        result = CliRunner().invoke(
            cli,
            ["exec", "Fix it", "--runner", "codex", "--model", "gpt-5.2"],
        )

    assert result.exit_code == 0, result.stderr
    assert commands[0][0] == "codex"
    assert "gpt-5.2" in commands[0]


def test_exec_attempts_the_task_once(workspace):
    """A one-shot must not silently spend three agent runs."""
    attempts = []

    def record(cmd, tasks_file, task_id, *args, **kwargs):
        attempts.append(task_id)
        return _finish("Failed.", status=tasks.TaskStatus.FAILED)(
            cmd, tasks_file, task_id, *args, **kwargs
        )

    with mock.patch("lemming.runner.run_with_heartbeat", record):
        CliRunner().invoke(cli, ["exec", "Fix the flaky test"])

    assert len(attempts) == 1


def test_exec_runs_in_the_directory_it_was_invoked_from(workspace):
    """Ephemeral state must not make the agent edit an ephemeral workspace."""
    seen = {}

    def record(cmd, tasks_file, task_id, *args, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return _finish("Done.")(cmd, tasks_file, task_id, *args, **kwargs)

    with mock.patch("lemming.runner.run_with_heartbeat", record):
        CliRunner().invoke(cli, ["exec", "Fix the flaky test"])

    assert seen["cwd"] == workspace.resolve()


def test_exec_reports_a_runner_that_produced_no_message(workspace):
    """Silence is not success; the caller still needs an outcome."""

    def silent(cmd, tasks_file, task_id, *args, **kwargs):
        paths.get_log_file(tasks_file, task_id).write_text("", encoding="utf-8")
        tasks.update_task(
            tasks_file, task_id, status=tasks.TaskStatus.COMPLETED, force=True
        )
        return 0, "", ""

    with mock.patch("lemming.runner.run_with_heartbeat", silent):
        result = CliRunner().invoke(cli, ["exec", "Fix the flaky test"])

    assert result.exit_code == 0
    assert result.stdout.strip() == ""
    assert "no closing message" in result.stderr.lower()
