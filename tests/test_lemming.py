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

    def test_delete_task(self):
        self.runner.invoke(cli, self.base_args + ["add", "To be removed"])

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            task_id = next(
                t["id"] for t in data["tasks"] if t["description"] == "To be removed"
            )

        delete_result = self.runner.invoke(cli, self.base_args + ["delete", task_id])
        self.assertEqual(delete_result.exit_code, 0)
        self.assertIn("Removed task", delete_result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            task_descs = [t["description"] for t in data["tasks"]]
            self.assertNotIn("To be removed", task_descs)

    def test_task_complete(self):
        result = self.runner.invoke(
            cli, self.base_args + ["complete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["tasks"][0]["status"], "completed")

    def test_task_complete_with_outcome(self):
        result = self.runner.invoke(
            cli, self.base_args + ["complete", "12345678", "--outcome", "Did the thing"]
        )
        self.assertEqual(result.exit_code, 0)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["tasks"][0]["status"], "completed")
            self.assertIn("Did the thing", data["tasks"][0]["lessons"])

    def test_task_fail(self):
        result = self.runner.invoke(
            cli,
            self.base_args
            + [
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

    def test_edit_task_description(self):
        result = self.runner.invoke(
            cli, self.base_args + ["edit", "12345678", "--description", "Updated Task"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task 12345678 updated.", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["tasks"][0]["description"], "Updated Task")

    def test_edit_task_agent(self):
        result = self.runner.invoke(
            cli, self.base_args + ["edit", "12345678", "--agent", "custom-agent"]
        )
        self.assertEqual(result.exit_code, 0)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["tasks"][0]["agent"], "custom-agent")

    def test_edit_task_index(self):
        # Add another task
        self.runner.invoke(cli, self.base_args + ["add", "Second Task"])
        
        # Get the ID of the second task
        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            second_task_id = data["tasks"][1]["id"]
        
        # Move second task to index 0
        result = self.runner.invoke(
            cli, self.base_args + ["edit", second_task_id, "--index", "0"]
        )
        self.assertEqual(result.exit_code, 0)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["tasks"][0]["id"], second_task_id)
            self.assertEqual(data["tasks"][1]["id"], "12345678")

    def test_edit_task_no_args(self):
        result = self.runner.invoke(cli, self.base_args + ["edit", "12345678"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Error: At least one of --description, --agent, or --index must be provided.", result.output)


    def test_add_task_with_options(self):
        result = self.runner.invoke(cli, self.base_args + ["add", "Agent Task", "--agent", "test-agent", "--index", "0"])
        self.assertEqual(result.exit_code, 0)
        
        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["tasks"][0]["description"], "Agent Task")
            self.assertEqual(data["tasks"][0]["agent"], "test-agent")

    def test_edit_task_not_found(self):
        result = self.runner.invoke(cli, self.base_args + ["edit", "nonexistent", "--description", "New"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Error: Task nonexistent not found.", result.output)

    def test_delete_task_not_found(self):
        result = self.runner.invoke(cli, self.base_args + ["delete", "nonexistent"])
        self.assertEqual(result.exit_code, 0) # Current implementation doesn't exit with 1
        self.assertIn("Error: Task nonexistent not found.", result.output)

    def test_info_task_not_found(self):
        result = self.runner.invoke(cli, self.base_args + ["info", "nonexistent"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Error: Task nonexistent not found.", result.output)

    def test_info_with_lessons_and_agent(self):
        # Setup task with lessons and agent
        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            data["tasks"][0]["lessons"] = ["Lesson 1"]
            data["tasks"][0]["agent"] = "special-agent"
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
            
        result = self.runner.invoke(cli, self.base_args + ["info", "12345678"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Custom Agent: special-agent", result.output)
        self.assertIn("--- Lessons Learned ---", result.output)
        self.assertIn("- Lesson 1", result.output)

    def test_info_with_outcomes(self):
        # Setup task with outcomes (now lessons)
        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            data["tasks"][0]["lessons"] = ["Outcome 1"]
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
            
        result = self.runner.invoke(cli, self.base_args + ["info", "12345678"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--- Lessons Learned ---", result.output)
        self.assertIn("- Outcome 1", result.output)

    def test_task_complete_not_found(self):
        result = self.runner.invoke(cli, self.base_args + ["complete", "nonexistent"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Error: Task nonexistent not found.", result.output)

    def test_task_uncomplete(self):
        # First mark as complete
        self.runner.invoke(cli, self.base_args + ["complete", "12345678"])
        
        result = self.runner.invoke(
            cli, self.base_args + ["uncomplete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("marked as pending", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["tasks"][0]["status"], "pending")

    def test_task_uncomplete_not_found(self):
        result = self.runner.invoke(cli, self.base_args + ["uncomplete", "nonexistent"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Error: Task nonexistent not found.", result.output)

    def test_task_fail_not_found(self):
        result = self.runner.invoke(cli, self.base_args + ["fail", "nonexistent", "--lesson", "why"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Error: Task nonexistent not found.", result.output)

    def test_reset_task(self):
        # Setup task with attempts and lessons
        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            data["tasks"][0]["attempts"] = 2
            data["tasks"][0]["lessons"] = ["Lesson 1"]
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        result = self.runner.invoke(cli, self.base_args + ["reset", "12345678"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("attempts and lessons cleared", result.output)

        with open(self.test_tasks_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self.assertEqual(data["tasks"][0]["attempts"], 0)
            self.assertEqual(data["tasks"][0]["lessons"], [])

    def test_reset_task_not_found(self):
        result = self.runner.invoke(cli, self.base_args + ["reset", "nonexistent"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Error: Task nonexistent not found.", result.output)

    def test_invalid_yaml(self):
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            f.write("invalid: yaml: :")
        
        result = self.runner.invoke(cli, self.base_args + ["info"])
        self.assertNotEqual(result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
