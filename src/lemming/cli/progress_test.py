import pathlib
import shutil
import tempfile
import unittest
import click.testing
from lemming import cli
from lemming import tasks


class TestCLIProgress(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--verbose", "--tasks-file", str(self.test_tasks_file)]

        # Scaffold a valid file
        data = tasks.Roadmap(
            context="Initial context",
            tasks=[
                tasks.Task(
                    id="12345678",
                    description="Initial Task",
                    status=tasks.TaskStatus.PENDING,
                    attempts=0,
                    progress=[],
                )
            ],
        )
        tasks.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_progress_add(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["progress", "12345678", "Observed behavior X"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Progress added to task", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIn("Observed behavior X", data.tasks[0].progress)

    def test_progress_list(self):
        self.cli_runner.invoke(cli.cli, self.base_args + ["progress", "12345678", "O1"])
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["progress", "list", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("[0] O1", result.output)

    def test_progress_edit(self):
        self.cli_runner.invoke(cli.cli, self.base_args + ["progress", "12345678", "O1"])
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["progress", "edit", "12345678", "0", "New O1"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Progress 0 updated", result.output)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].progress[0], "New O1")

    def test_progress_delete(self):
        self.cli_runner.invoke(cli.cli, self.base_args + ["progress", "12345678", "O1"])
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["progress", "delete", "12345678", "0"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Progress 0 deleted", result.output)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(len(data.tasks[0].progress), 0)


if __name__ == "__main__":
    unittest.main()
