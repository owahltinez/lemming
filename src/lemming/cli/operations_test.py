import pathlib
import shutil
import tempfile
import time
import unittest
from unittest import mock

import click.testing

from lemming import cli, tasks


class TestCLIOperations(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = [
            "--verbose",
            "--tasks-file",
            str(self.test_tasks_file),
        ]

        # Scaffold a valid file
        data = tasks.Roadmap(
            goal="Initial goal",
            tasks=[],
        )
        tasks.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_run_help(self):
        result = self.cli_runner.invoke(cli.cli, ["run", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Starts the orchestrator loop", result.output)

    def test_run_empty_queue_reports_completion(self):
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["run"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("All tasks completed!", result.output)

    def test_run_blocked_queue_exits_nonzero(self):
        tasks.save_tasks(
            self.test_tasks_file,
            tasks.Roadmap(
                tasks=[
                    tasks.Task(
                        id="active123",
                        description="Active task",
                        status=tasks.TaskStatus.IN_PROGRESS,
                        pid=1234,
                        last_heartbeat=time.time(),
                    ),
                    tasks.Task(
                        id="pending456",
                        description="Pending task",
                        status=tasks.TaskStatus.PENDING,
                    ),
                ]
            ),
        )

        with mock.patch(
            "lemming.tasks.lifecycle.is_pid_alive", return_value=True
        ):
            result = self.cli_runner.invoke(cli.cli, self.base_args + ["run"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Queue blocked by active task active123", result.output)
        self.assertIn("1 pending task remains", result.output)
        self.assertNotIn("All tasks completed!", result.output)

    def test_run_rejects_live_loop_owner(self):
        tasks.acquire_loop_lock(self.test_tasks_file)
        try:
            result = self.cli_runner.invoke(cli.cli, self.base_args + ["run"])
        finally:
            tasks.release_loop_lock(self.test_tasks_file)

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Another loop is already running", result.output)

    def test_serve_help(self):
        result = self.cli_runner.invoke(cli.cli, ["serve", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Launches the local web dashboard", result.output)


if __name__ == "__main__":
    unittest.main()
