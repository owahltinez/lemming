import pathlib
import shutil
import tempfile
import unittest
import yaml

from click.testing import CliRunner

from lemming.main import cli


class TestLemming(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]

        # Scaffold a valid file
        data = {
            "context": "Initial context",
            "tasks": [
                {
                    "id": "12345678",
                    "description": "Initial Task",
                    "status": "pending",
                    "attempts": 0,
                    "lessons": [],
                }
            ],
        }
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_add_task(self):
        result = self.runner.invoke(cli, self.base_args + ["add", "New Task"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Added task", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            task_descs = [t["description"] for t in data["tasks"]]
            self.assertIn("New Task", task_descs)

    def test_list_tasks(self):
        self.runner.invoke(cli, self.base_args + ["add", "Task 1"])
        self.runner.invoke(cli, self.base_args + ["add", "Task 2"])
        result = self.runner.invoke(cli, self.base_args + ["list"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task 1", result.output)
        self.assertIn("Task 2", result.output)

    def test_rm_task(self):
        self.runner.invoke(cli, self.base_args + ["add", "To be removed"])

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            task_id = next(
                t["id"] for t in data["tasks"] if t["description"] == "To be removed"
            )

        rm_result = self.runner.invoke(cli, self.base_args + ["rm", task_id])
        self.assertEqual(rm_result.exit_code, 0)
        self.assertIn("Removed task", rm_result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            task_descs = [t["description"] for t in data["tasks"]]
            self.assertNotIn("To be removed", task_descs)

    def test_task_complete(self):
        result = self.runner.invoke(
            cli, self.base_args + ["task", "complete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["tasks"][0]["status"], "completed")

    def test_task_fail(self):
        result = self.runner.invoke(
            cli,
            self.base_args
            + [
                "task",
                "fail",
                "12345678",
                "--lesson",
                "Failed due to missing dependency",
            ],
        )
        self.assertEqual(result.exit_code, 0)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["tasks"][0]["status"], "pending")
            self.assertIn(
                "Failed due to missing dependency", data["tasks"][0]["lessons"]
            )

    def test_info_no_args(self):
        result = self.runner.invoke(cli, self.base_args + ["info"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("=== Project Context ===", result.output)
        self.assertIn("Initial context", result.output)
        self.assertIn("=== Tasks ===", result.output)
        self.assertIn("(12345678) Initial Task", result.output)

    def test_info_with_id(self):
        result = self.runner.invoke(cli, self.base_args + ["info", "12345678"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task ID:     12345678", result.output)
        self.assertIn("Status:      pending", result.output)
        self.assertIn("Description: Initial Task", result.output)

    def test_context_no_args(self):
        result = self.runner.invoke(cli, self.base_args + ["context"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Initial context", result.output)

    def test_set_context(self):
        result = self.runner.invoke(cli, self.base_args + ["context", "Updated context via CLI"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Project context updated.", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["context"], "Updated context via CLI")

    def test_set_context_from_file(self):
        context_file = pathlib.Path(self.test_dir) / "context.txt"
        context_file.write_text("Context from file content", encoding="utf-8")

        result = self.runner.invoke(cli, self.base_args + ["context", "--file", str(context_file)])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Project context updated.", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["context"], "Context from file content")


if __name__ == "__main__":
    unittest.main()
