import logging.config
import pathlib
import shutil
import tempfile
import time
import unittest
from unittest import mock

import click.testing

from lemming import models, persistence
from lemming.cli import main as cli
from lemming.cli import operations


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
        data = models.Roadmap(
            goal="Initial goal",
            tasks=[],
        )
        persistence.save_tasks(self.test_tasks_file, data)

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
        persistence.save_tasks(
            self.test_tasks_file,
            models.Roadmap(
                tasks=[
                    models.Task(
                        id="active123",
                        description="Active task",
                        status=models.TaskStatus.IN_PROGRESS,
                        pid=1234,
                        last_heartbeat=time.time(),
                    ),
                    models.Task(
                        id="pending456",
                        description="Pending task",
                        status=models.TaskStatus.PENDING,
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
        persistence.acquire_loop_lock(self.test_tasks_file)
        try:
            result = self.cli_runner.invoke(cli.cli, self.base_args + ["run"])
        finally:
            persistence.release_loop_lock(self.test_tasks_file)

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Another loop is already running", result.output)

    def test_run_forwards_max_tasks(self):
        with mock.patch.object(
            operations, "run_loop", return_value=True
        ) as run_loop:
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["run", "--max-tasks", "2"]
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(run_loop.call_args.kwargs["max_tasks"], 2)

    def test_run_resolves_until_prefix(self):
        persistence.save_tasks(
            self.test_tasks_file,
            models.Roadmap(
                tasks=[models.Task(id="abcd1234", description="Milestone")]
            ),
        )

        with mock.patch.object(
            operations, "run_loop", return_value=True
        ) as run_loop:
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["run", "--until", "abcd"]
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(run_loop.call_args.kwargs["until"], "abcd1234")

    def test_run_rejects_unknown_until(self):
        with mock.patch.object(operations, "run_loop") as run_loop:
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["run", "--until", "nope"]
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Task nope not found", result.output)
        run_loop.assert_not_called()

    def test_run_rejects_zero_max_tasks(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["run", "--max-tasks", "0"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not in the range", result.output)

    def test_serve_help(self):
        result = self.cli_runner.invoke(cli.cli, ["serve", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Launches the local web dashboard", result.output)


class TestQuietPollLogConfig(unittest.TestCase):
    def test_filter_path_resolves(self):
        """`serve` names the filter by string; nothing else resolves it."""
        log_config = operations.quiet_poll_log_config()
        factory = log_config["filters"]["quiet_poll"]["()"]

        # resolve() is what dictConfig calls, minus the global side effects.
        logging.config.BaseConfigurator({}).resolve(factory)


if __name__ == "__main__":
    unittest.main()
