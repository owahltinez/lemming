import pathlib
import shutil
import tempfile
import unittest
import yaml

from click.testing import CliRunner

from lemming.main import cli


class TestLemmingClear(unittest.TestCase):
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
                    "lessons": [],
                }
            ],
        }
        self.reset_data()

    def reset_data(self):
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(self.initial_data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_clear_default(self):
        result = self.runner.invoke(cli, self.base_args + ["clear"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Cleared task queue.", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["context"], "Initial context")
            self.assertEqual(data["tasks"], [])

    def test_clear_all(self):
        result = self.runner.invoke(cli, self.base_args + ["clear", "--all"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Cleared all context and tasks.", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["context"], "")
            self.assertEqual(data["tasks"], [])

    def test_clear_tasks(self):
        result = self.runner.invoke(cli, self.base_args + ["clear", "--tasks"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Cleared task queue.", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["context"], "Initial context")
            self.assertEqual(data["tasks"], [])

    def test_clear_context(self):
        result = self.runner.invoke(cli, self.base_args + ["clear", "--context"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Cleared project context.", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["context"], "")
            self.assertEqual(len(data["tasks"]), 1)

    def test_clear_tasks_and_context(self):
        result = self.runner.invoke(cli, self.base_args + ["clear", "--tasks", "--context"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Cleared all context and tasks.", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["context"], "")
            self.assertEqual(data["tasks"], [])

if __name__ == "__main__":
    unittest.main()
