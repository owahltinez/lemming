import pathlib
import shutil
import tempfile
import unittest

from click.testing import CliRunner

from lemming.cli import main as cli
from lemming import tasks


class TestCliProgress(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]
        self.cli_runner = CliRunner()

        # Scaffold a valid file with one task
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
        tasks.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_progress(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["progress", "12345678", "Observed behavior X"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Progress added to task", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIn("Observed behavior X", data.tasks[0].progress)


if __name__ == "__main__":
    unittest.main()
