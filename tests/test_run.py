import pathlib
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import yaml

from click.testing import CliRunner
from lemming.main import cli

class TestLemmingRun(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]

        # Scaffold a valid file with one task
        self.initial_data = {
            "context": "Initial context",
            "tasks": [
                {
                    "id": "task1",
                    "description": "Task 1",
                    "status": "pending",
                    "attempts": 0,
                    "lessons": [],
                }
            ],
        }
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(self.initial_data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @patch("subprocess.run")
    def test_run_success(self, mock_run):
        # Simulate agent reporting success
        def side_effect(*args, **kwargs):
            # Manually update the file to simulate the task being completed
            with open(self.test_tasks_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            data["tasks"][0]["status"] = "completed"
            with open(self.test_tasks_file, "w", encoding="utf-8") as f:
                yaml.dump(data, f)
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect

        result = self.runner.invoke(cli, ["--verbose"] + self.base_args + ["run", "--max-attempts", "1"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("All tasks completed!", result.output)
        self.assertIn("Agent successfully reported task completion.", result.output)

    @patch("subprocess.run")
    @patch("time.sleep", return_value=None) # Skip delay
    def test_run_retry_and_fail(self, mock_sleep, mock_run):
        # Agent finishes but doesn't report completion
        mock_run.return_value = MagicMock(returncode=0)

        result = self.runner.invoke(cli, self.base_args + ["run", "--max-attempts", "2", "--retry-delay", "0"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task task1 failed after 2 attempts. Aborting run.", result.output)
        self.assertEqual(mock_run.call_count, 2)

    @patch("subprocess.run")
    def test_run_subprocess_error(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(1, "cmd")

        # It should retry if status is still pending
        result = self.runner.invoke(cli, self.base_args + ["run", "--max-attempts", "1"])
        self.assertIn("execution failed with exit code 1", result.output)
        self.assertIn("Task task1 failed after 1 attempts. Aborting run.", result.output)

    @patch("subprocess.run")
    def test_run_command_not_found(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(127, "cmd")

        result = self.runner.invoke(cli, self.base_args + ["run", "--max-attempts", "1"])
        self.assertIn("execution failed with exit code 127", result.output)
        self.assertIn("NOTE: Command 'gemini' not found.", result.output)

if __name__ == "__main__":
    unittest.main()
