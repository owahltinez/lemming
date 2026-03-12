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
                    "outcomes": [],
                }
            ],
        }
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(self.initial_data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @patch("subprocess.Popen")
    def test_run_success(self, mock_popen):
        # Simulate agent reporting success
        mock_process = MagicMock()
        mock_process.poll.side_effect = [
            None,
            0,
        ]  # First poll None (running), second poll 0 (finished)
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("stdout", "stderr")
        mock_popen.return_value = mock_process

        # We need to update the file to simulate the task being completed
        # But we do it when communicate is called, or in side effect of poll
        def poll_side_effect():
            if mock_process.poll.call_count > 1:
                with open(self.test_tasks_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                data["tasks"][0]["status"] = "completed"
                with open(self.test_tasks_file, "w", encoding="utf-8") as f:
                    yaml.dump(data, f)
                return 0
            return None

        mock_process.poll.side_effect = poll_side_effect

        result = self.runner.invoke(
            cli, ["--verbose"] + self.base_args + ["run", "--max-attempts", "1"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("All tasks completed!", result.output)
        self.assertIn("Agent successfully reported task completion.", result.output)

    @patch("subprocess.Popen")
    @patch("time.sleep", return_value=None)  # Skip delay
    def test_run_retry_and_fail(self, mock_sleep, mock_popen):
        # Agent finishes but doesn't report completion
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("stdout", "stderr")
        mock_popen.return_value = mock_process

        result = self.runner.invoke(
            cli, self.base_args + ["run", "--max-attempts", "2", "--retry-delay", "0"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn(
            "Task task1 failed after 2 attempts. Aborting run.", result.output
        )
        self.assertEqual(mock_popen.call_count, 2)

    @patch("subprocess.Popen")
    def test_run_subprocess_error(self, mock_popen):
        mock_process = MagicMock()
        mock_process.poll.return_value = 1
        mock_process.returncode = 1
        mock_process.communicate.return_value = ("stdout", "stderr")
        mock_popen.return_value = mock_process

        # It should retry if status is still pending
        result = self.runner.invoke(
            cli, self.base_args + ["run", "--max-attempts", "1"]
        )
        # With CalledProcessError, it might not be raised directly but handled in the catch
        # In the new code, it's raised after communicate() if returncode != 0
        self.assertIn("execution failed with exit code 1", result.output)
        self.assertIn(
            "Task task1 failed after 1 attempts. Aborting run.", result.output
        )

    @patch("subprocess.Popen")
    def test_run_command_not_found(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError(
            2, "No such file or directory", "gemini"
        )

        result = self.runner.invoke(
            cli, self.base_args + ["run", "--max-attempts", "1"]
        )
        self.assertIn("An error occurred while executing gemini", result.output)


if __name__ == "__main__":
    unittest.main()
