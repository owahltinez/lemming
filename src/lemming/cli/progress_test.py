import time

import pytest
from click.testing import CliRunner

from lemming import models, persistence
from lemming.cli import main as cli
from lemming.tasks import limits


@pytest.fixture
def setup_env(tmp_path):
    tasks_file = tmp_path / "tasks_test.yml"
    base_args = ["--tasks-file", str(tasks_file)]
    runner = CliRunner()

    data = models.Roadmap(
        goal="Initial goal",
        tasks=[
            models.Task(
                id="12345678",
                description="Test task",
                status=models.TaskStatus.PENDING,
            )
        ],
    )
    persistence.save_tasks(tasks_file, data)

    return runner, base_args, tasks_file


def test_progress(setup_env):
    runner, base_args, tasks_file = setup_env

    result = runner.invoke(
        cli.cli, base_args + ["progress", "12345678", "Observed behavior X"]
    )

    assert result.exit_code == 0
    assert "Progress added to task" in result.output

    data = persistence.load_tasks(tasks_file)
    assert "Observed behavior X" in data.tasks[0].progress


def test_progress_reports_actionable_size_error(setup_env):
    runner, base_args, tasks_file = setup_env

    result = runner.invoke(
        cli.cli,
        base_args
        + [
            "progress",
            "12345678",
            "x" * (limits.MAX_PROGRESS_ENTRY_CHARS + 1),
        ],
    )

    assert result.exit_code == 1
    assert "281 characters (limit 280)" in result.output
    assert "lemming artifact <id> <name> --file -" in result.output
    assert str(tasks_file.parent) not in result.output
    assert persistence.load_tasks(tasks_file).tasks[0].progress == []


@pytest.mark.parametrize(
    ("component", "time_limit", "elapsed_s", "expected"),
    [
        ("runner", 30, 20, "(<1m/30m elapsed, 29m remaining)."),
        ("runner", 30, 22 * 60, "(22m/30m elapsed, 8m remaining)."),
        ("runner", 30, 29 * 60 + 30, "(29m/30m elapsed, <1m remaining)."),
        ("runner", 30, 31 * 60, "(31m/30m elapsed, time limit reached)."),
        ("hook:testing", 60, 5 * 60, "(5m/15m elapsed, 10m remaining)."),
        ("hook:testing", 10, 5 * 60, "(5m/10m elapsed, 5m remaining)."),
        ("runner", 0, 10 * 60, "Progress added to task 12345678."),
    ],
)
def test_progress_reports_elapsed_time_budget(
    setup_env, monkeypatch, component, time_limit, elapsed_s, expected
):
    runner, base_args, tasks_file = setup_env
    now = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)

    data = persistence.load_tasks(tasks_file)
    data.config.time_limit = time_limit
    data.tasks[0].status = models.TaskStatus.RUNNING
    data.tasks[0].active_execution_component = component
    data.tasks[0].active_execution_started_at = now - elapsed_s
    persistence.save_tasks(tasks_file, data)

    result = runner.invoke(
        cli.cli, base_args + ["progress", "12345678", "checkpoint"]
    )

    assert result.exit_code == 0
    assert result.output.strip().endswith(expected)
