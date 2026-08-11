"""Tests for the one-shot exec command."""

import json
import subprocess
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

    A real agent settles its task by invoking the CLI, which records the
    outcome as a request for the hooks to apply rather than applying it
    outright. Completing the task directly would skip the hooks entirely,
    so the fake goes through the same request.
    """
    output = json.dumps({"type": "result", "result": message}) + "\n"

    def fake(cmd, tasks_file, task_id, *args, **kwargs):
        log_file = paths.get_log_file(tasks_file, task_id)
        log_file.write_text(f"Command: {cmd[0]}\n{output}", encoding="utf-8")

        # A hook runs against a task that already asked to finish; asking
        # again would be the hook overriding the agent it is reviewing.
        data = tasks.load_tasks(tasks_file)
        task = next(t for t in data.tasks if t.id == task_id)
        if task.requested_status:
            return returncode, output, ""

        tasks.add_progress(tasks_file, task_id, "did the work")
        tasks.update_task(tasks_file, task_id, status=status.value)
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


def _git(working_dir, *args):
    """Runs a git command in the given directory."""
    subprocess.run(
        ["git", *args], cwd=working_dir, check=True, capture_output=True
    )


@pytest.fixture
def repo(workspace):
    """A workspace that is also a git repository with one commit."""
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    (workspace / "committed.py").write_text("x = 1\n")
    _git(workspace, "add", "committed.py")
    _git(workspace, "commit", "-qm", "initial")
    return workspace


def test_review_runs_the_hook_without_a_task_runner(repo):
    """A review of existing work has nothing for a runner to do."""
    (repo / "committed.py").write_text("x = 2\n")
    headers = []

    def record(cmd, tasks_file, task_id, *args, **kwargs):
        headers.append(kwargs.get("header"))
        return _finish("Reviewed.")(cmd, tasks_file, task_id, *args, **kwargs)

    with mock.patch("lemming.runner.run_with_heartbeat", record):
        result = CliRunner().invoke(cli, ["exec", "--review", "readability"])

    assert result.exit_code == 0, result.stderr
    assert headers == ["Hook: readability"]


def test_review_tells_the_agent_what_changed(repo):
    """The scope is resolved up front, not left for the agent to guess."""
    (repo / "committed.py").write_text("x = 2\n")
    prompts_seen = []

    def record(cmd, tasks_file, task_id, *args, **kwargs):
        prompts_seen.append(cmd[-1])
        return _finish("Reviewed.")(cmd, tasks_file, task_id, *args, **kwargs)

    with mock.patch("lemming.runner.run_with_heartbeat", record):
        CliRunner().invoke(cli, ["exec", "--review", "readability"])

    assert "- committed.py" in prompts_seen[0]


def test_review_accepts_an_explicit_scope(repo):
    """A path is passed through for the agent to act on."""
    prompts_seen = []

    def record(cmd, tasks_file, task_id, *args, **kwargs):
        prompts_seen.append(cmd[-1])
        return _finish("Reviewed.")(cmd, tasks_file, task_id, *args, **kwargs)

    with mock.patch("lemming.runner.run_with_heartbeat", record):
        result = CliRunner().invoke(
            cli, ["exec", "--review", "readability", "--scope", "src/api/"]
        )

    assert result.exit_code == 0, result.stderr
    assert "- src/api/" in prompts_seen[0]


def test_review_refuses_the_orchestration_hook(repo):
    """Roadmap revision would add tasks to a roadmap about to be deleted."""
    result = CliRunner().invoke(cli, ["exec", "--review", "roadmap"])

    assert result.exit_code != 0
    assert "roadmap" in result.stderr.lower()


def test_review_all_excludes_the_orchestration_band(repo):
    """--review all must not require naming every review by hand."""
    (repo / "committed.py").write_text("x = 2\n")
    headers = []

    def record(cmd, tasks_file, task_id, *args, **kwargs):
        headers.append(kwargs.get("header"))
        return _finish("Reviewed.")(cmd, tasks_file, task_id, *args, **kwargs)

    with mock.patch("lemming.runner.run_with_heartbeat", record):
        CliRunner().invoke(cli, ["exec", "--review", "all"])

    assert "Hook: roadmap" not in headers
    assert "Hook: readability" in headers


def test_review_stops_when_nothing_changed(repo):
    """A clean tree means no review to run, not a review of everything."""
    calls = []

    def record(cmd, tasks_file, task_id, *args, **kwargs):
        calls.append(cmd)
        return _finish("Reviewed.")(cmd, tasks_file, task_id, *args, **kwargs)

    with mock.patch("lemming.runner.run_with_heartbeat", record):
        result = CliRunner().invoke(cli, ["exec", "--review", "readability"])

    assert calls == []
    assert "no changes" in result.stderr.lower()


def test_review_rejects_an_unresolvable_scope(repo):
    """A typo must fail loudly rather than review the wrong thing."""
    result = CliRunner().invoke(
        cli,
        ["exec", "--review", "readability", "--scope", "no-branch...HEAD"],
    )

    assert result.exit_code != 0


def test_a_task_can_be_followed_by_reviews(repo):
    """Doing the work and gating it is one invocation, in order."""
    headers = []

    def record(cmd, tasks_file, task_id, *args, **kwargs):
        headers.append(kwargs.get("header"))
        return _finish("Done.")(cmd, tasks_file, task_id, *args, **kwargs)

    with mock.patch("lemming.runner.run_with_heartbeat", record):
        CliRunner().invoke(
            cli, ["exec", "Add pagination", "--review", "readability"]
        )

    assert headers == ["Task Runner", "Hook: readability"]
