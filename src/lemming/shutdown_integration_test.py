"""End-to-end shutdown tests that drive a real `lemming run` subprocess."""

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from lemming import models, persistence, shutdown, tasks

# Bounds for polling the loop's on-disk state; generous enough for a cold
# interpreter start, short enough to keep the suite fast.
STARTUP_TIMEOUT_SECONDS = 30
SHUTDOWN_TIMEOUT_SECONDS = 15


def _is_alive(pid: int) -> bool:
    """Returns whether a PID is still running."""
    return persistence.is_pid_alive(pid)


class TestShutdownIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = pathlib.Path(tempfile.mkdtemp())
        self.tasks_file = self.test_dir / "tasks.yml"

        # A runner that sleeps far longer than the test so we can observe
        # whether it outlives the orchestrator.
        self.fake_runner = self.test_dir / "fake_runner.sh"
        self.fake_runner.write_text("#!/bin/bash\nsleep 300\n")
        self.fake_runner.chmod(0o755)

        tasks.save_tasks(
            self.tasks_file,
            models.Roadmap(
                goal="Shutdown test",
                tasks=[models.Task(id="task1", description="Long task")],
                config=models.RoadmapConfig(
                    retries=1, runner=str(self.fake_runner)
                ),
            ),
        )
        self.loop: subprocess.Popen | None = None

    def tearDown(self):
        if self.loop and self.loop.poll() is None:
            self.loop.kill()
            self.loop.wait(timeout=10)
        runner_pid = self._task_pid()
        if runner_pid and _is_alive(runner_pid):
            os.kill(runner_pid, signal.SIGKILL)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _start_loop(self) -> subprocess.Popen:
        """Starts `lemming run` against the temporary project."""
        self.loop = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "lemming.main",
                "--tasks-file",
                str(self.tasks_file),
                "run",
                "--no-defaults",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=self.test_dir,
        )
        return self.loop

    def _task_pid(self) -> int | None:
        """Reads the runner PID the loop recorded for the task."""
        if not self.tasks_file.exists():
            return None
        data = tasks.load_tasks(self.tasks_file)
        return data.tasks[0].pid if data.tasks else None

    def _wait_for_runner(self) -> int:
        """Blocks until the loop records a live runner PID."""
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            pid = self._task_pid()
            if pid and _is_alive(pid):
                return pid
            time.sleep(0.2)
        self.fail("Runner process was never started")

    def _wait_until_dead(self, pid: int, what: str) -> None:
        """Blocks until a PID exits, failing if it outlives the timeout."""
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not _is_alive(pid):
                return
            time.sleep(0.2)
        self.fail(f"{what} (pid {pid}) survived shutdown")

    def test_sigterm_does_not_orphan_the_runner(self):
        """SIGTERM to the loop must take the runner down with it."""
        loop = self._start_loop()
        runner_pid = self._wait_for_runner()

        loop.send_signal(signal.SIGTERM)

        self._wait_until_dead(runner_pid, "Runner child")
        loop.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)

    def _run_cli(self, *args) -> subprocess.CompletedProcess:
        """Runs a lemming CLI command against the temporary project."""
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "lemming.main",
                "--tasks-file",
                str(self.tasks_file),
                *args,
            ],
            capture_output=True,
            text=True,
            cwd=self.test_dir,
            timeout=60,
            check=False,
        )

    def test_stop_kills_the_runner_and_requeues_the_task(self):
        """`lemming stop` leaves no orphan and no blocked queue."""
        loop = self._start_loop()
        runner_pid = self._wait_for_runner()

        result = self._run_cli("stop")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._wait_until_dead(runner_pid, "Runner child")
        loop.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)

        task = tasks.load_tasks(self.tasks_file).tasks[0]
        self.assertEqual(task.status, models.TaskStatus.PENDING)
        self.assertIsNone(task.pid)

    def test_stop_reports_when_nothing_is_running(self):
        """Stopping an idle project succeeds without touching state."""
        result = self._run_cli("stop")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No orchestrator loop", result.stdout)

    def test_stop_after_current_task_drains_without_claiming_more(self):
        """Draining finishes the task, then skips the rest of the queue."""
        # A runner that exits on its own, so the current task can complete.
        self.fake_runner.write_text("#!/bin/bash\nsleep 3\n")
        data = tasks.load_tasks(self.tasks_file)
        data.tasks.append(models.Task(id="task2", description="Second task"))
        tasks.save_tasks(self.tasks_file, data)

        loop = self._start_loop()
        self._wait_for_runner()
        result = self._run_cli("stop", "--after-current-task")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        loop.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)

        second = tasks.load_tasks(self.tasks_file).tasks[1]
        self.assertEqual(second.id, "task2")
        self.assertEqual(second.status, models.TaskStatus.PENDING)
        self.assertEqual(second.attempts, 0)

    def test_drain_signal_leaves_runner_running(self):
        """A drain request lets the in-flight task finish undisturbed."""
        loop = self._start_loop()
        runner_pid = self._wait_for_runner()

        loop.send_signal(shutdown.DRAIN_SIGNAL)
        time.sleep(3)

        self.assertTrue(
            _is_alive(runner_pid),
            "Drain must not kill the task that is already running",
        )


if __name__ == "__main__":
    unittest.main()
