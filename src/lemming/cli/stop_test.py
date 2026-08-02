"""Tests for the `lemming stop` command."""

import os
import pathlib
import shutil
import signal
import tempfile
import unittest
from unittest import mock

import click.testing

from lemming import cli, shutdown, tasks

_WAIT_TARGET = "lemming.cli.operations._wait_for_loop_exit"


class TestCLIStop(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.tasks_file = pathlib.Path(self.test_dir) / "tasks.yml"
        self.base_args = ["--tasks-file", str(self.tasks_file)]
        tasks.save_tasks(
            self.tasks_file,
            tasks.Roadmap(
                goal="g",
                tasks=[
                    tasks.Task(
                        id="task1",
                        description="running task",
                        status=tasks.TaskStatus.IN_PROGRESS,
                        pid=424242,
                    )
                ],
            ),
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _invoke(self, *extra):
        return self.cli_runner.invoke(
            cli.cli, self.base_args + ["stop", *extra]
        )

    def test_reports_when_no_loop_is_running(self):
        """Stopping an idle project is a clear no-op, not an error."""
        result = self._invoke()

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No orchestrator loop", result.output)

    def test_sends_terminate_signal_to_the_loop(self):
        """The default stop asks the loop to shut down immediately."""
        tasks.acquire_loop_lock(self.tasks_file)
        try:
            with (
                mock.patch("os.kill") as mock_kill,
                mock.patch(_WAIT_TARGET, return_value=True),
            ):
                result = self._invoke()
        finally:
            tasks.release_loop_lock(self.tasks_file)

        self.assertEqual(result.exit_code, 0, result.output)
        mock_kill.assert_any_call(os.getpid(), signal.SIGTERM)

    def test_after_current_task_sends_drain_signal(self):
        """Draining leaves the running task alone."""
        tasks.acquire_loop_lock(self.tasks_file)
        try:
            with mock.patch("os.kill") as mock_kill:
                result = self._invoke("--after-current-task")
        finally:
            tasks.release_loop_lock(self.tasks_file)

        self.assertEqual(result.exit_code, 0, result.output)
        mock_kill.assert_any_call(os.getpid(), shutdown.DRAIN_SIGNAL)

    def test_drain_does_not_strand_the_running_task(self):
        """A drain must not touch the in-flight task's state."""
        tasks.acquire_loop_lock(self.tasks_file)
        try:
            with mock.patch("os.kill"):
                self._invoke("--after-current-task")
        finally:
            tasks.release_loop_lock(self.tasks_file)

        task = tasks.load_tasks(self.tasks_file).tasks[0]
        self.assertEqual(task.status, tasks.TaskStatus.IN_PROGRESS)

    def test_immediate_stop_releases_the_queue(self):
        """A stopped task returns to pending instead of blocking the queue."""
        tasks.acquire_loop_lock(self.tasks_file)
        try:
            with (
                mock.patch("os.kill"),
                mock.patch(_WAIT_TARGET, return_value=True),
            ):
                result = self._invoke()
        finally:
            tasks.release_loop_lock(self.tasks_file)

        self.assertEqual(result.exit_code, 0, result.output)
        task = tasks.load_tasks(self.tasks_file).tasks[0]
        self.assertEqual(task.status, tasks.TaskStatus.PENDING)
        self.assertIsNone(task.pid)


if __name__ == "__main__":
    unittest.main()
