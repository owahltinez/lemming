import pathlib
import shutil
import tempfile
import unittest
import yaml

from click.testing import CliRunner

from lemming.main import cli


class TestLemmingDeleteBulk(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--verbose", "--tasks-file", str(self.test_tasks_file)]

        # Scaffold a valid file
        self.initial_data = {
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
        self.reset_data()

    def reset_data(self):
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(self.initial_data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_delete_all(self):
        result = self.runner.invoke(cli, self.base_args + ["delete", "--all"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Deleted all tasks and cleared context.", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["context"], "")
            self.assertEqual(data["tasks"], [])

    def test_delete_completed(self):
        # Setup data with mixed tasks
        data = {
            "context": "Initial context",
            "tasks": [
                {"id": "t1", "description": "Completed", "status": "completed"},
                {"id": "t2", "description": "Pending", "status": "pending"},
            ],
        }
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        result = self.runner.invoke(cli, self.base_args + ["delete", "--completed"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Deleted 1 completed task(s).", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(len(data["tasks"]), 1)
            self.assertEqual(data["tasks"][0]["id"], "t2")
            self.assertEqual(data["context"], "Initial context")

    def test_delete_no_args_shows_error(self):
        result = self.runner.invoke(cli, self.base_args + ["delete"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Provide a task ID", result.output)

    def test_delete_all_and_completed_mutually_exclusive(self):
        result = self.runner.invoke(
            cli, self.base_args + ["delete", "--all", "--completed"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("mutually exclusive", result.output)

    def test_delete_task_id_with_all_flag_shows_error(self):
        result = self.runner.invoke(
            cli, self.base_args + ["delete", "12345678", "--all"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Cannot specify a task ID", result.output)

    def test_delete_preserves_context_with_completed(self):
        """Ensure --completed only removes completed tasks, not context."""
        data = {
            "context": "Keep this",
            "tasks": [
                {"id": "t1", "description": "Done", "status": "completed"},
            ],
        }
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        self.runner.invoke(cli, self.base_args + ["delete", "--completed"])

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["context"], "Keep this")
            self.assertEqual(data["tasks"], [])


if __name__ == "__main__":
    unittest.main()
