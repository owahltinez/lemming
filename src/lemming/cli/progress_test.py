import pytest
from click.testing import CliRunner

from lemming.cli import main as cli
from lemming import tasks


@pytest.fixture
def setup_env(tmp_path):
    tasks_file = tmp_path / "tasks_test.yml"
    base_args = ["--tasks-file", str(tasks_file)]
    runner = CliRunner()

    data = tasks.Roadmap(
        context="Initial context",
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
