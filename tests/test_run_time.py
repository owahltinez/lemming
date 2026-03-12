import pathlib
import shutil
import tempfile
import unittest
import yaml
import time

from click.testing import CliRunner
from lemming.main import cli
from lemming.core import mark_task_in_progress, load_tasks


class TestRunTime(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--verbose", "--tasks-file", str(self.test_tasks_file)]

        # Scaffold a valid file
        data = {
            "context": "Initial context",
            "tasks": [
                {
                    "id": "12345678",
                    "description": "Initial Task",
                    "status": "pending",
                    "attempts": 0,
                    "outcomes": [],
                }
            ],
        }
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_run_time_complete(self):
        # Mark in progress
        mark_task_in_progress(self.test_tasks_file, "12345678")

        # Wait a bit to accumulate run time
        time.sleep(0.2)

        # Record outcome and complete
        self.runner.invoke(cli, self.base_args + ["outcome", "12345678", "Done"])
        self.runner.invoke(cli, self.base_args + ["complete", "12345678"])

        data = load_tasks(self.test_tasks_file)
        task = data["tasks"][0]
        self.assertIn("run_time", task)
        self.assertGreaterEqual(task["run_time"], 0.2)

        # Check status output
        result = self.runner.invoke(cli, self.base_args + ["status", "12345678"])
        self.assertIn("Run Time:", result.output)
        # Should show seconds as it's < 60s
        self.assertIn("s", result.output)

    def test_run_time_fail(self):
        # Mark in progress
        mark_task_in_progress(self.test_tasks_file, "12345678")

        # Wait a bit
        time.sleep(0.1)

        # Record outcome and fail
        self.runner.invoke(cli, self.base_args + ["outcome", "12345678", "Failed"])
        self.runner.invoke(cli, self.base_args + ["fail", "12345678"])

        data = load_tasks(self.test_tasks_file)
        task = data["tasks"][0]
        self.assertIn("run_time", task)
        self.assertGreaterEqual(task["run_time"], 0.1)

    def test_run_time_cumulative(self):
        # First attempt
        mark_task_in_progress(self.test_tasks_file, "12345678")
        time.sleep(0.1)
        self.runner.invoke(cli, self.base_args + ["outcome", "12345678", "Failed 1"])
        self.runner.invoke(cli, self.base_args + ["fail", "12345678"])

        # Second attempt
        mark_task_in_progress(self.test_tasks_file, "12345678")
        time.sleep(0.1)
        self.runner.invoke(cli, self.base_args + ["outcome", "12345678", "Done"])
        self.runner.invoke(cli, self.base_args + ["complete", "12345678"])

        data = load_tasks(self.test_tasks_file)
        task = data["tasks"][0]
        self.assertIn("run_time", task)
        self.assertGreaterEqual(task["run_time"], 0.2)

    def test_run_time_reset(self):
        mark_task_in_progress(self.test_tasks_file, "12345678")
        time.sleep(0.1)
        self.runner.invoke(cli, self.base_args + ["outcome", "12345678", "Done"])
        self.runner.invoke(cli, self.base_args + ["complete", "12345678"])

        # Reset
        self.runner.invoke(cli, self.base_args + ["reset", "12345678"])

        data = load_tasks(self.test_tasks_file)
        task = data["tasks"][0]
        self.assertEqual(task.get("run_time", 0), 0)

    def test_run_time_loop(self):
        # Run lemming run with a simple agent that does nothing
        # It should still record some run time for the attempt
        self.runner.invoke(
            cli,
            self.base_args + ["run", "--agent", "echo", "--max-attempts", "1", "hello"],
        )

        data = load_tasks(self.test_tasks_file)
        task = data["tasks"][0]
        self.assertIn("run_time", task)
        self.assertGreaterEqual(task["run_time"], 0)
        self.assertGreaterEqual(task["attempts"], 1)
        self.assertEqual(task["status"], "pending")


if __name__ == "__main__":
    unittest.main()
