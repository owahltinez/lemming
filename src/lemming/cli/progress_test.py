import pytest
from click.testing import CliRunner

from lemming import tasks
from lemming.cli import main as cli


@pytest.fixture
def setup_env(tmp_path):
    tasks_file = tmp_path / "tasks_test.yml"
    base_args = ["--tasks-file", str(tasks_file)]
    runner = CliRunner()

    data = tasks.Roadmap(
        goal="Initial goal",
        tasks=[
            tasks.Task(
                id="12345678",
                description="Test task",
                status=tasks.TaskStatus.PENDING,
            )
        ],
    )
    tasks.save_tasks(tasks_file, data)

    return runner, base_args, tasks_file


def test_progress(setup_env):
    runner, base_args, tasks_file = setup_env

    result = runner.invoke(
        cli.cli, base_args + ["progress", "12345678", "Observed behavior X"]
    )

    assert result.exit_code == 0
    assert "Progress added to task" in result.output

    data = tasks.load_tasks(tasks_file)
    assert "Observed behavior X" in data.tasks[0].progress


def test_progress_reports_actionable_size_error(setup_env):
    runner, base_args, tasks_file = setup_env

    result = runner.invoke(
        cli.cli,
        base_args
        + [
            "progress",
            "12345678",
            "x" * (tasks.MAX_PROGRESS_ENTRY_CHARS + 1),
        ],
    )

    assert result.exit_code == 1
    assert "281 characters (limit 280)" in result.output
    assert "Write detailed evidence or verbose command output" in result.output
    assert str(tasks_file.parent) not in result.output
    assert tasks.load_tasks(tasks_file).tasks[0].progress == []
