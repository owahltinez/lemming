import pathlib
import shutil
import tempfile
import unittest
from unittest import mock
import click.testing
from lemming import cli
from lemming import tasks


class TestCLITasks(unittest.TestCase):
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
                    outcomes=[],
                )
            ],
        )
        tasks.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_add_task(self):
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["add", "New Task"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Added task", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        task_descs = [t.description for t in data.tasks]
        self.assertIn("New Task", task_descs)

    def test_edit_task_description(self):
        result = self.cli_runner.invoke(
            cli.cli,
            self.base_args + ["edit", "12345678", "--description", "Updated Task"],
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task 12345678 updated.", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].description, "Updated Task")

    def test_delete_task(self):
        self.cli_runner.invoke(cli.cli, self.base_args + ["add", "To be removed"])

        data = tasks.load_tasks(self.test_tasks_file)
        task_id = next(t.id for t in data.tasks if t.description == "To be removed")

        delete_result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["delete", task_id]
        )
        self.assertEqual(delete_result.exit_code, 0)
        self.assertIn("Removed task", delete_result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        task_descs = [t.description for t in data.tasks]
        self.assertNotIn("To be removed", task_descs)

    def test_status_command(self):
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["status"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Loop Status: Idle", result.output)
        self.assertIn("Initial Task", result.output)

        # Verify Running state (mocking is_loop_running)
        with mock.patch("lemming.tasks.lifecycle.is_loop_running", return_value=True):
            result = self.cli_runner.invoke(cli.cli, self.base_args + ["status"])
            self.assertIn("Loop Status: Running", result.output)

    def test_logs_command_fail_no_logs(self):
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["logs", "12345678"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No log for task", result.output)

    def test_task_complete(self):
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["outcome", "12345678", "Done"]
        )
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["complete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.COMPLETED)

    def test_task_uncomplete(self):
        # First complete it
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["outcome", "12345678", "Done"]
        )
        self.cli_runner.invoke(cli.cli, self.base_args + ["complete", "12345678"])

        # Then uncomplete
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["uncomplete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.PENDING)

    def test_task_fail(self):
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["outcome", "12345678", "Failed reason"]
        )
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["fail", "12345678"])
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.FAILED)

    def test_cancel_command(self):
        # We need a fake in-progress task for this
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks[0].status = tasks.TaskStatus.IN_PROGRESS
            tasks.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["cancel", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task 12345678 cancelled.", result.output)

    def test_reset_command(self):
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["reset", "12345678"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("attempts, outcomes, and logs cleared", result.output)


if __name__ == "__main__":
    unittest.main()
