import hashlib
import os
import pathlib
import shutil
import tempfile
import time
import unittest
import unittest.mock

import yaml
import click.testing

from lemming import runner
from lemming import api
from lemming import main
from lemming import paths
from lemming import tasks


class TestLemming(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
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

    def test_add_task(self):
        result = self.cli_runner.invoke(main.cli, self.base_args + ["add", "New Task"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Added task", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        task_descs = [t.description for t in data.tasks]
        self.assertIn("New Task", task_descs)

    def test_delete_task(self):
        self.cli_runner.invoke(main.cli, self.base_args + ["add", "To be removed"])

        data = tasks.load_tasks(self.test_tasks_file)
        task_id = next(t.id for t in data.tasks if t.description == "To be removed")

        delete_result = self.cli_runner.invoke(
            main.cli, self.base_args + ["delete", task_id]
        )
        self.assertEqual(delete_result.exit_code, 0)
        self.assertIn("Removed task", delete_result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        task_descs = [t.description for t in data.tasks]
        self.assertNotIn("To be removed", task_descs)

    def test_task_complete(self):
        self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Done"]
        )
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["complete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, "completed")

    def test_task_complete_after_outcome(self):
        # Record outcome first
        self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Did the thing"]
        )
        # Then complete
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["complete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, "completed")
        self.assertIn("Did the thing", data.tasks[0].outcomes)

    def test_task_fail(self):
        # Record failure outcome first
        self.cli_runner.invoke(
            main.cli,
            self.base_args
            + [
                "outcome",
                "12345678",
                "Failed due to missing dependency",
            ],
        )
        # Then fail
        result = self.cli_runner.invoke(
            main.cli,
            self.base_args
            + [
                "fail",
                "12345678",
            ],
        )
        self.assertEqual(result.exit_code, 0)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, "pending")
        self.assertIn("Failed due to missing dependency", data.tasks[0].outcomes)

    def test_info_no_args(self):
        result = self.cli_runner.invoke(main.cli, self.base_args + ["status"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("=== Project Context ===", result.output)
        self.assertIn("Initial context", result.output)
        self.assertIn("=== Tasks ===", result.output)
        self.assertIn("(12345678) Initial Task", result.output)

    def test_info_with_id(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["status", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task ID:     12345678", result.output)
        self.assertIn("Status:      pending", result.output)
        self.assertIn("Description: Initial Task", result.output)

    def test_context_no_args(self):
        result = self.cli_runner.invoke(main.cli, self.base_args + ["context"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Initial context", result.output)

    def test_set_context(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["context", "Updated context via CLI"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Project context updated.", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.context, "Updated context via CLI")

    def test_set_context_from_file(self):
        context_file = pathlib.Path(self.test_dir) / "context.txt"
        context_file.write_text("Context from file content", encoding="utf-8")

        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["context", "--file", str(context_file)]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Project context updated.", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.context, "Context from file content")

    def test_edit_task_description(self):
        result = self.cli_runner.invoke(
            main.cli,
            self.base_args + ["edit", "12345678", "--description", "Updated Task"],
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task 12345678 updated.", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].description, "Updated Task")

    def test_edit_task_runner(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["edit", "12345678", "--runner", "custom-runner"]
        )
        self.assertEqual(result.exit_code, 0)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].runner, "custom-runner")

    def test_edit_task_index(self):
        # Add another task
        self.cli_runner.invoke(main.cli, self.base_args + ["add", "Second Task"])

        # Get the ID of the second task
        data = tasks.load_tasks(self.test_tasks_file)
        second_task_id = data.tasks[1].id

        # Move second task to index 0
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["edit", second_task_id, "--index", "0"]
        )
        self.assertEqual(result.exit_code, 0)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].id, second_task_id)
        self.assertEqual(data.tasks[1].id, "12345678")

    def test_edit_task_no_args(self):
        result = self.cli_runner.invoke(main.cli, self.base_args + ["edit", "12345678"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn(
            "Error: At least one of --description, --runner, --index, --parent, or --parent-tasks-file must be provided.",
            result.output,
        )

    def test_clear_tasks_by_default(self):
        # Add a task
        self.cli_runner.invoke(main.cli, self.base_args + ["add", "Task to clear"])
        # Set context
        self.cli_runner.invoke(
            main.cli, self.base_args + ["context", "Context to keep"]
        )

        # Run clear without flags
        result = self.cli_runner.invoke(
            main.cli, ["--tasks-file", str(self.test_tasks_file), "delete", "--all"]
        )
        self.assertEqual(result.exit_code, 0)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks, [])
        # In our current implementation, delete --all also clears context
        self.assertEqual(data.context, "")

    def test_uncomplete_command(self):
        # 1. Complete it
        self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Done"]
        )
        self.cli_runner.invoke(main.cli, self.base_args + ["complete", "12345678"])

        # 2. Uncomplete it
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["uncomplete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("marked as pending", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, "pending")

    def test_outcome_command(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Observed behavior X"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Outcome added to task", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIn("Observed behavior X", data.tasks[0].outcomes)

    def test_add_task_file(self):
        with open("desc.txt", "w") as f:
            f.write("Task from file")
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["add", "-f", "desc.txt"]
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[-1].description, "Task from file")
        os.remove("desc.txt")

    def test_add_task_stdin(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["add", "-f", "-"], input="Task from stdin"
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[-1].description, "Task from stdin")

    def test_add_task_both_error(self):
        with open("desc.txt", "w") as f:
            f.write("Task from file")
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["add", "Description", "-f", "desc.txt"]
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn(
            "Error: Cannot provide both description and --file.", result.output
        )
        os.remove("desc.txt")

    def test_add_task_neither_error(self):
        result = self.cli_runner.invoke(main.cli, self.base_args + ["add"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn(
            "Error: Must provide either description or --file.", result.output
        )

    def test_edit_task_file(self):
        with open("new_desc.txt", "w") as f:
            f.write("Updated description")
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["edit", "12345678", "-f", "new_desc.txt"]
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].description, "Updated description")
        os.remove("new_desc.txt")

    def test_edit_task_both_error(self):
        with open("new_desc.txt", "w") as f:
            f.write("Updated description")
        result = self.cli_runner.invoke(
            main.cli,
            self.base_args
            + ["edit", "12345678", "--description", "Desc", "-f", "new_desc.txt"],
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn(
            "Error: Cannot provide both description and --file.", result.output
        )
        os.remove("new_desc.txt")

    def test_outcome_add_file(self):
        with open("outcome.txt", "w") as f:
            f.write("Outcome from file")
        result = self.cli_runner.invoke(
            main.cli,
            self.base_args + ["outcome", "add", "12345678", "-f", "outcome.txt"],
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIn("Outcome from file", data.tasks[0].outcomes)
        os.remove("outcome.txt")

    def test_outcome_add_stdin(self):
        result = self.cli_runner.invoke(
            main.cli,
            self.base_args + ["outcome", "add", "12345678", "-f", "-"],
            input="Outcome from stdin",
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIn("Outcome from stdin", data.tasks[0].outcomes)

    def test_outcome_add_both_error(self):
        with open("outcome.txt", "w") as f:
            f.write("Outcome from file")
        result = self.cli_runner.invoke(
            main.cli,
            self.base_args
            + ["outcome", "add", "12345678", "Desc", "-f", "outcome.txt"],
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn(
            "Error: Cannot provide both outcome text and --file.", result.output
        )
        os.remove("outcome.txt")

    def test_outcome_add_neither_error(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "add", "12345678"]
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn(
            "Error: Must provide either outcome text or --file.", result.output
        )

    def test_outcome_edit_file(self):
        data = tasks.load_tasks(self.test_tasks_file)
        data.tasks[0].outcomes = ["original"]
        tasks.save_tasks(self.test_tasks_file, data)

        with open("edited.txt", "w") as f:
            f.write("edited outcome")
        result = self.cli_runner.invoke(
            main.cli,
            self.base_args + ["outcome", "edit", "12345678", "0", "-f", "edited.txt"],
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].outcomes[0], "edited outcome")
        os.remove("edited.txt")

    def test_outcome_edit_both_error(self):
        data = tasks.load_tasks(self.test_tasks_file)
        data.tasks[0].outcomes = ["original"]
        tasks.save_tasks(self.test_tasks_file, data)

        with open("edited.txt", "w") as f:
            f.write("edited outcome")
        result = self.cli_runner.invoke(
            main.cli,
            self.base_args
            + ["outcome", "edit", "12345678", "0", "New Desc", "-f", "edited.txt"],
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn(
            "Error: Cannot provide both outcome text and --file.", result.output
        )
        os.remove("edited.txt")

    def test_outcome_edit_neither_error(self):
        data = tasks.load_tasks(self.test_tasks_file)
        data.tasks[0].outcomes = ["original"]
        tasks.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "edit", "12345678", "0"]
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn(
            "Error: Must provide either outcome text or --file.", result.output
        )

    def test_reset_command(self):
        # 1. Add some state
        data = tasks.load_tasks(self.test_tasks_file)
        data.tasks[0].attempts = 5
        data.tasks[0].outcomes = ["outcome 1"]
        data.tasks[0].status = "completed"
        tasks.save_tasks(self.test_tasks_file, data)

        # 2. Reset
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["reset", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("attempts, outcomes, and logs cleared", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].attempts, 0)
        self.assertEqual(data.tasks[0].outcomes, [])
        self.assertEqual(data.tasks[0].status, "pending")

    def test_add_task_with_runner(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["add", "Runner Task", "--runner", "aider"]
        )
        self.assertEqual(result.exit_code, 0)

        data = tasks.load_tasks(self.test_tasks_file)
        new_task = next(t for t in data.tasks if t.description == "Runner Task")
        self.assertEqual(new_task.runner, "aider")

    def test_add_task_with_index(self):
        self.cli_runner.invoke(
            main.cli, self.base_args + ["add", "First", "--index", "0"]
        )  # This will be at 0, old 12345678 will be at 1
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["add", "Middle", "--index", "1"]
        )
        self.assertEqual(result.exit_code, 0)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].description, "First")
        self.assertEqual(data.tasks[1].description, "Middle")
        self.assertEqual(data.tasks[2].description, "Initial Task")

    def test_complete_requires_outcome(self):
        # 1. Try to complete without outcome
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["complete", "12345678"]
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("has no recorded outcomes", result.output)

        # 2. Add outcome and try again
        self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Done"]
        )
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["complete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)

    def test_fail_requires_outcome(self):
        # 1. Try to fail without outcome
        result = self.cli_runner.invoke(main.cli, self.base_args + ["fail", "12345678"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("has no recorded outcomes", result.output)

        # 2. Add outcome and try again
        self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Failed"]
        )
        result = self.cli_runner.invoke(main.cli, self.base_args + ["fail", "12345678"])
        self.assertEqual(result.exit_code, 0)

    def test_load_prompt(self):
        prompt = runner.load_prompt("taskrunner")
        self.assertIn("roadmap", prompt)
        self.assertIn("description", prompt)

    def test_prepare_prompt(self):
        data = tasks.Roadmap(
            context="Context",
            tasks=[tasks.Task(id="1", description="T1", status="pending")],
        )
        prompt = runner.prepare_prompt(data, data.tasks[0], self.test_tasks_file)
        self.assertIn("T1", prompt)
        self.assertIn("Context", prompt)

        self.assertIn("roadmap", prompt.lower())

    def test_prompt_replacement_logic(self):
        template = "Hello {{name}}, welcome to {{place}}!"
        prompt = template.replace("{{name}}", "World").replace("{{place}}", "Lemming")
        self.assertEqual(prompt, "Hello World, welcome to Lemming!")

    def test_verbose_info(self):
        # Test without verbose (should be quiet by default)
        result = self.cli_runner.invoke(
            main.cli, ["--tasks-file", str(self.test_tasks_file), "status"]
        )
        self.assertNotIn("=== Project Context ===", result.output)
        # Pending tasks are SHOWN by default even in non-verbose
        self.assertIn("Initial Task", result.output)

        # Let's make it completed
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks[0].status = "completed"
            tasks.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            main.cli, ["--tasks-file", str(self.test_tasks_file), "status"]
        )
        self.assertNotIn("Initial Task", result.output)
        self.assertIn("(1 completed tasks hidden)", result.output)

        # Test with verbose
        result_v = self.cli_runner.invoke(
            main.cli, ["--verbose", "--tasks-file", str(self.test_tasks_file), "status"]
        )
        self.assertIn("=== Project Context ===", result_v.output)
        self.assertIn("Initial Task", result_v.output)

    def test_verbose_add(self):
        # Default is quiet (just ID)
        result = self.cli_runner.invoke(
            main.cli, ["--tasks-file", str(self.test_tasks_file), "add", "new task"]
        )
        self.assertEqual(len(result.output.strip()), 8)  # hex ID of length 8
        self.assertNotIn("Added task", result.output)

        # Verbose shows the message
        result_v = self.cli_runner.invoke(
            main.cli,
            [
                "--verbose",
                "--tasks-file",
                str(self.test_tasks_file),
                "add",
                "another task",
            ],
        )
        self.assertIn("Added task", result_v.output)

    def test_run_default_quiet(self):
        # Run is quiet by default, but should still report attempt
        self.cli_runner.invoke(
            main.cli,
            [
                "--tasks-file",
                str(self.test_tasks_file),
                "config",
                "set",
                "runner",
                "true",
            ],
        )
        self.cli_runner.invoke(
            main.cli,
            [
                "--tasks-file",
                str(self.test_tasks_file),
                "config",
                "set",
                "retries",
                "1",
            ],
        )
        result = self.cli_runner.invoke(
            main.cli,
            [
                "--tasks-file",
                str(self.test_tasks_file),
                "run",
            ],
        )
        self.assertIn("[12345678] Attempt 1/1: Initial Task", result.output)
        self.assertNotIn("--- Task 12345678", result.output)

    def test_run_verbose_global(self):
        # Run with global verbose shows more
        self.cli_runner.invoke(
            main.cli,
            [
                "--tasks-file",
                str(self.test_tasks_file),
                "config",
                "set",
                "runner",
                "true",
            ],
        )
        self.cli_runner.invoke(
            main.cli,
            [
                "--tasks-file",
                str(self.test_tasks_file),
                "config",
                "set",
                "retries",
                "1",
            ],
        )
        result = self.cli_runner.invoke(
            main.cli,
            [
                "--verbose",
                "--tasks-file",
                str(self.test_tasks_file),
                "run",
            ],
        )
        self.assertIn("--- Task 12345678", result.output)
        self.assertIn("=== Runner Prompt ===", result.output)

    def test_run_attempts_limit(self):
        # Run with retries=2. The runner 'true' does not use lemming CLI
        # to complete the task, so it counts as an execution without completion.
        self.cli_runner.invoke(
            main.cli,
            [
                "--tasks-file",
                str(self.test_tasks_file),
                "config",
                "set",
                "runner",
                "true",
            ],
        )
        self.cli_runner.invoke(
            main.cli,
            [
                "--tasks-file",
                str(self.test_tasks_file),
                "config",
                "set",
                "retries",
                "2",
            ],
        )
        result = self.cli_runner.invoke(
            main.cli,
            [
                "--tasks-file",
                str(self.test_tasks_file),
                "run",
                "--retry-delay",
                "0",
            ],
        )
        self.assertIn("failed after 2 attempts", result.output)

        # Bug verification: attempts should be exactly retries (2), not 3.
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].attempts, 2)

    def test_run_time_complete(self):
        # Mark in progress
        tasks.mark_task_in_progress(self.test_tasks_file, "12345678")

        # Wait a bit to accumulate run time
        time.sleep(0.2)

        # Record outcome and complete
        self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Done"]
        )
        self.cli_runner.invoke(main.cli, self.base_args + ["complete", "12345678"])

        data = tasks.load_tasks(self.test_tasks_file)
        task = data.tasks[0]
        self.assertGreaterEqual(task.run_time, 0.2)

        # Check status output
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["status", "12345678"]
        )
        self.assertIn("Run Time:", result.output)
        self.assertIn("s", result.output)

    def test_run_time_fail(self):
        # Mark in progress
        tasks.mark_task_in_progress(self.test_tasks_file, "12345678")

        # Wait a bit
        time.sleep(0.1)

        # Record outcome and fail
        self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Failed"]
        )
        self.cli_runner.invoke(main.cli, self.base_args + ["fail", "12345678"])

        data = tasks.load_tasks(self.test_tasks_file)
        task = data.tasks[0]
        self.assertGreaterEqual(task.run_time, 0.1)

    def test_run_time_cumulative(self):
        # First attempt
        tasks.mark_task_in_progress(self.test_tasks_file, "12345678")
        time.sleep(0.1)
        self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Failed 1"]
        )
        self.cli_runner.invoke(main.cli, self.base_args + ["fail", "12345678"])

        # Second attempt
        tasks.mark_task_in_progress(self.test_tasks_file, "12345678")
        time.sleep(0.1)
        self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Done"]
        )
        self.cli_runner.invoke(main.cli, self.base_args + ["complete", "12345678"])

        data = tasks.load_tasks(self.test_tasks_file)
        task = data.tasks[0]
        self.assertGreaterEqual(task.run_time, 0.2)

    def test_run_time_reset(self):
        tasks.mark_task_in_progress(self.test_tasks_file, "12345678")
        time.sleep(0.1)
        self.cli_runner.invoke(
            main.cli, self.base_args + ["outcome", "12345678", "Done"]
        )
        self.cli_runner.invoke(main.cli, self.base_args + ["complete", "12345678"])

        # Reset
        self.cli_runner.invoke(main.cli, self.base_args + ["reset", "12345678"])

        data = tasks.load_tasks(self.test_tasks_file)
        task = data.tasks[0]
        self.assertEqual(task.run_time, 0)

    def test_delete_all(self):
        result = self.cli_runner.invoke(main.cli, self.base_args + ["delete", "--all"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Deleted all tasks", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.context, "")
        self.assertEqual(data.tasks, [])

    def test_delete_completed(self):
        # Setup data with mixed tasks
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks.append(
                tasks.Task(id="t1", description="Completed", status="completed")
            )
            data.tasks.append(
                tasks.Task(id="t2", description="Pending", status="pending")
            )
            tasks.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["delete", "--completed"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Deleted 1 completed task(s)", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        task_ids = [t.id for t in data.tasks]
        self.assertNotIn("t1", task_ids)
        self.assertIn("t2", task_ids)

    def test_delete_no_args_shows_error(self):
        result = self.cli_runner.invoke(main.cli, self.base_args + ["delete"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Provide a task ID", result.output)

    def test_delete_all_and_completed_mutually_exclusive(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["delete", "--all", "--completed"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("mutually exclusive", result.output)

    def test_gemini_full_path(self):
        cmd = runner.build_runner_command("/usr/bin/gemini", "hello", yolo=True)
        self.assertEqual(cmd[0], "/usr/bin/gemini")
        self.assertIn("--yolo", cmd)
        self.assertNotIn("--quiet", cmd)
        self.assertIn("hello", cmd)

    def test_aider_full_path(self):
        cmd = runner.build_runner_command("/opt/aider", "hello", yolo=True)
        self.assertEqual(cmd[0], "/opt/aider")
        self.assertIn("--yes", cmd)
        self.assertIn("--quiet", cmd)
        self.assertIn("--message", cmd)
        self.assertIn("hello", cmd)

    def test_claude_yolo(self):
        cmd = runner.build_runner_command("claude", "hello", yolo=True)
        self.assertEqual(cmd[0], "claude")
        self.assertIn("--dangerously-skip-permissions", cmd)
        self.assertIn("--print", cmd)
        self.assertIn("hello", cmd)

    def test_codex_yolo(self):
        cmd = runner.build_runner_command("codex", "hello", yolo=True)
        self.assertEqual(cmd[0], "codex")
        self.assertIn("--yolo", cmd)
        self.assertIn("--instructions", cmd)
        self.assertIn("hello", cmd)

    def test_fuzzy_gemini_match(self):
        cmd = runner.build_runner_command("gemini-v2", "hello", yolo=True)
        self.assertEqual(cmd[0], "gemini-v2")
        self.assertIn("--yolo", cmd)
        self.assertIn("--no-sandbox", cmd)
        self.assertNotIn("--quiet", cmd)
        self.assertIn("--prompt", cmd)
        self.assertEqual(cmd[-1], "hello")

    def test_no_defaults_flag(self):
        cmd = runner.build_runner_command(
            "gemini", "hello", yolo=True, no_defaults=True
        )
        self.assertEqual(cmd, ["gemini", "hello"])

    def test_template_prompt_arg(self):
        cmd = runner.build_runner_command(
            "custom-runner --input {{prompt}}", "hello", yolo=True
        )
        self.assertEqual(cmd, ["custom-runner", "--input", "hello"])

    def test_template_prompt_in_flag(self):
        cmd = runner.build_runner_command(
            "custom-runner --input={{prompt}}", "hello", yolo=True
        )
        self.assertEqual(cmd, ["custom-runner", "--input=hello"])

    def test_runner_args(self):
        cmd = runner.build_runner_command(
            "gemini", "hello", yolo=True, runner_args=("--model", "flash")
        )
        self.assertEqual(cmd[0], "gemini")
        self.assertIn("--model", cmd)
        self.assertIn("flash", cmd)
        self.assertIn("hello", cmd)
        self.assertEqual(cmd[-1], "hello")

    def test_cancel_task(self):
        import subprocess

        # Start a dummy process that sleeps in a new session
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        pid = proc.pid

        data = tasks.Roadmap(
            context="test",
            tasks=[
                tasks.Task(
                    id="task_cancel",
                    description="task cancel",
                    status="in_progress",
                    pid=pid,
                    last_heartbeat=time.time(),
                )
            ],
        )
        tasks.save_tasks(self.test_tasks_file, data)

        # Verify process is running
        self.assertIsNone(proc.poll())

        # Cancel the task
        self.assertTrue(tasks.cancel_task(self.test_tasks_file, "task_cancel"))

        # Give it a moment to die
        time.sleep(0.1)

        # Verify process is killed
        self.assertIsNotNone(proc.poll())

        # Verify task status is pending and PID is removed
        updated_data = tasks.load_tasks(self.test_tasks_file)
        task = updated_data.tasks[0]
        self.assertEqual(task.status, "pending")
        self.assertIsNone(task.pid)
        self.assertIsNone(task.last_heartbeat)

    def test_cancel_task_no_pid(self):
        data = tasks.Roadmap(
            context="test",
            tasks=[
                tasks.Task(
                    id="task_cancel_no_pid",
                    description="task cancel no pid",
                    status="in_progress",
                    last_heartbeat=time.time(),
                )
            ],
        )
        tasks.save_tasks(self.test_tasks_file, data)

        self.assertTrue(tasks.cancel_task(self.test_tasks_file, "task_cancel_no_pid"))

        updated_data = tasks.load_tasks(self.test_tasks_file)
        task = updated_data.tasks[0]
        self.assertEqual(task.status, "pending")
        self.assertIsNone(task.last_heartbeat)

    def test_get_pending_task_normal(self):
        data = tasks.Roadmap(
            tasks=[tasks.Task(id="t1", description="Task 1", status="pending")]
        )
        task = tasks.get_pending_task(data)
        self.assertIsNotNone(task)
        self.assertEqual(task.id, "t1")

    def test_get_pending_task_in_progress_not_stale(self):
        data = tasks.Roadmap(
            tasks=[
                tasks.Task(
                    id="t1",
                    description="Task 1",
                    status="in_progress",
                    last_heartbeat=time.time(),
                )
            ]
        )
        task = tasks.get_pending_task(data)
        self.assertIsNone(task)

    def test_get_pending_task_in_progress_stale(self):
        data = tasks.Roadmap(
            tasks=[
                tasks.Task(
                    id="t1",
                    description="Task 1",
                    status="in_progress",
                    last_heartbeat=time.time() - (tasks.STALE_THRESHOLD + 1),
                )
            ]
        )
        task = tasks.get_pending_task(data)
        self.assertIsNotNone(task)
        self.assertEqual(task.id, "t1")

    def test_get_pending_task_dead_pid(self):
        import subprocess

        p = subprocess.Popen(["true"])
        p.wait()
        dead_pid = p.pid

        data = tasks.Roadmap(
            tasks=[
                tasks.Task(
                    id="t1",
                    description="Task 1",
                    status="in_progress",
                    last_heartbeat=time.time(),
                    pid=dead_pid,
                )
            ]
        )
        task = tasks.get_pending_task(data)
        self.assertIsNotNone(task)
        self.assertEqual(task.id, "t1")

    def test_get_pending_task_alive_pid(self):
        alive_pid = os.getpid()

        data = tasks.Roadmap(
            tasks=[
                tasks.Task(
                    id="t1",
                    description="Task 1",
                    status="in_progress",
                    last_heartbeat=time.time(),
                    pid=alive_pid,
                )
            ]
        )
        task = tasks.get_pending_task(data)
        self.assertIsNone(task)

    def test_status_sorting(self):
        # Setup tasks in a specific order
        # 1. Pending 1
        # 2. Completed 1 (earlier)
        # 3. In Progress 1
        # 4. Completed 2 (later)
        # 5. Pending 2

        data = {
            "context": "Context",
            "tasks": [
                {
                    "id": "p1",
                    "description": "Pending 1",
                    "status": "pending",
                    "attempts": 0,
                    "outcomes": [],
                },
                {
                    "id": "c1",
                    "description": "Completed 1",
                    "status": "completed",
                    "completed_at": 1000,
                    "attempts": 1,
                    "outcomes": ["done"],
                },
                {
                    "id": "i1",
                    "description": "In Progress 1",
                    "status": "in_progress",
                    "attempts": 1,
                    "outcomes": [],
                },
                {
                    "id": "c2",
                    "description": "Completed 2",
                    "status": "completed",
                    "completed_at": 2000,
                    "attempts": 1,
                    "outcomes": ["done"],
                },
                {
                    "id": "p2",
                    "description": "Pending 2",
                    "status": "pending",
                    "attempts": 0,
                    "outcomes": [],
                },
            ],
        }
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        result = self.cli_runner.invoke(main.cli, self.base_args + ["status"])
        self.assertEqual(result.exit_code, 0)

        # Order should be:
        # 1. Pending 2 (p2) (newest uncompleted)
        # 2. In Progress 1 (i1)
        # 3. Pending 1 (p1)
        # 4. Completed 2 (c2) (newest completed)
        # 5. Completed 1 (c1)

        lines = [
            line.strip()
            for line in result.output.split("\n")
            if line.strip() and not line.startswith("===")
        ]

        task_ids = []
        for line in lines:
            if "(" in line and ")" in line:
                task_id = line.split("(")[1].split(")")[0]
                task_ids.append(task_id)

        # Check expected order
        expected_order = ["p2", "i1", "p1", "c2", "c1"]
        self.assertEqual(task_ids, expected_order)


class TestTasksLocation(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        # Remove LEMMING_HOME if set by conftest to allow Path.home mocking
        self.env_patcher = unittest.mock.patch.dict(os.environ)
        self.env_patcher.start()
        if "LEMMING_HOME" in os.environ:
            del os.environ["LEMMING_HOME"]

    def tearDown(self):
        self.env_patcher.stop()

    @unittest.mock.patch("pathlib.Path.home")
    def test_default_location_no_local_file(self, mock_home):
        with self.cli_runner.isolated_filesystem() as td:
            temp_home = pathlib.Path(td).resolve() / "fake_home"
            temp_home.mkdir()
            mock_home.return_value = temp_home

            # The current working directory in isolated_filesystem will be some temp dir inside td
            cwd_path = str(pathlib.Path.cwd().resolve())
            path_hash = hashlib.sha256(cwd_path.encode()).hexdigest()[:12]

            self.cli_runner.invoke(main.cli, ["add", "Test Task"])

            expected_local_path = pathlib.Path("tasks.yml")
            expected_global_path = (
                temp_home / ".local" / "lemming" / path_hash / "tasks.yml"
            )

            # Desired behavior:
            self.assertFalse(expected_local_path.exists())
            self.assertTrue(expected_global_path.exists())

    @unittest.mock.patch("pathlib.Path.home")
    def test_different_directories_different_hashes(self, mock_home):
        with self.cli_runner.isolated_filesystem() as td:
            temp_home = pathlib.Path(td).resolve() / "fake_home"
            temp_home.mkdir()
            mock_home.return_value = temp_home

            # Create two different project directories
            proj1 = pathlib.Path(td) / "project1"
            proj2 = pathlib.Path(td) / "project2"
            proj1.mkdir()
            proj2.mkdir()

            # In proj1
            os.chdir(proj1)
            cwd1 = str(pathlib.Path.cwd().resolve())
            hash1 = hashlib.sha256(cwd1.encode()).hexdigest()[:12]
            self.cli_runner.invoke(main.cli, ["add", "Task 1"])
            path1 = temp_home / ".local" / "lemming" / hash1 / "tasks.yml"
            self.assertTrue(path1.exists())

            # In proj2
            os.chdir(proj2)
            cwd2 = str(pathlib.Path.cwd().resolve())
            hash2 = hashlib.sha256(cwd2.encode()).hexdigest()[:12]
            self.cli_runner.invoke(main.cli, ["add", "Task 2"])
            path2 = temp_home / ".local" / "lemming" / hash2 / "tasks.yml"
            self.assertTrue(path2.exists())

            self.assertNotEqual(hash1, hash2)
            self.assertNotEqual(path1, path2)


class TestLemmingRun(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
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

    @unittest.mock.patch("subprocess.Popen")
    def test_run_success(self, mock_popen):
        # Simulate runner reporting success
        mock_process = unittest.mock.MagicMock()
        mock_process.pid = 12345
        mock_process.poll.side_effect = [
            None,
            0,
        ]  # First poll None (running), second poll 0 (finished)
        mock_process.returncode = 0
        mock_process.stdout = iter(["stdout\n"])
        mock_process.communicate.return_value = ("stdout", "stderr")

        # We need to update the file to simulate the task being completed
        # But we do it when wait is called
        def wait_side_effect():
            with tasks.lock_tasks(self.test_tasks_file):
                data = tasks.load_tasks(self.test_tasks_file)
                data.tasks[0].status = "completed"
                data.tasks[0].completed_at = 123456789.0
                tasks.save_tasks(self.test_tasks_file, data)
            return 0

        mock_process.wait.side_effect = wait_side_effect
        mock_popen.return_value = mock_process

        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "retries", "1"]
        )
        result = self.cli_runner.invoke(
            main.cli, ["--verbose"] + self.base_args + ["run"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("All tasks completed!", result.output)

        self.assertIn("Runner successfully reported task completion.", result.output)

    @unittest.mock.patch("subprocess.Popen")
    @unittest.mock.patch("time.sleep", return_value=None)  # Skip delay
    def test_run_retry_and_fail(self, mock_sleep, mock_popen):
        # Runner finishes but doesn't report completion
        mock_process = unittest.mock.MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = 0
        mock_process.returncode = 0
        mock_process.stdout = iter(["stdout\n"])
        mock_process.communicate.return_value = ("stdout", "stderr")
        mock_popen.return_value = mock_process

        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "retries", "2"]
        )
        self.cli_runner.invoke(main.cli, self.base_args + ["hooks", "set", "roadmap"])
        result = self.cli_runner.invoke(
            main.cli,
            self.base_args + ["run", "--retry-delay", "0"],
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn(
            "Task task1 failed after 2 attempts. Aborting run.", result.output
        )
        # 2 attempts (Task + Hook) + 1 final failure Hook run = 5
        self.assertEqual(mock_popen.call_count, 5)

    @unittest.mock.patch("subprocess.Popen")
    def test_run_subprocess_error(self, mock_popen):
        mock_process = unittest.mock.MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = 1
        mock_process.returncode = 1
        mock_process.stdout = iter(["error output\n"])
        mock_process.communicate.return_value = ("stdout", "stderr")
        mock_popen.return_value = mock_process

        # It should retry if status is still pending
        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "retries", "1"]
        )
        result = self.cli_runner.invoke(main.cli, self.base_args + ["run"])
        self.assertIn("execution failed with exit code 1", result.output)
        self.assertIn(
            "Task task1 failed after 1 attempts. Aborting run.", result.output
        )

    @unittest.mock.patch("subprocess.Popen")
    def test_run_command_not_found(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError(
            2, "No such file or directory", "gemini"
        )

        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "retries", "1"]
        )
        result = self.cli_runner.invoke(main.cli, self.base_args + ["run"])
        self.assertIn("An error occurred while executing gemini", result.output)

    @unittest.mock.patch("subprocess.Popen")
    def test_run_recovers_stale_task(self, mock_popen):
        # 1. Mark a task as in_progress with a very old heartbeat
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks[0].status = "in_progress"
            data.tasks[0].last_heartbeat = time.time() - (tasks.STALE_THRESHOLD + 10)
            data.tasks[0].pid = 999999  # Some fake PID
            tasks.save_tasks(self.test_tasks_file, data)

        # 2. Setup mock for the runner
        mock_process = unittest.mock.MagicMock()
        mock_process.pid = 12345
        mock_process.poll.side_effect = [None, 0]
        mock_process.returncode = 0
        mock_process.stdout = iter(["success\n"])

        def wait_side_effect():
            # Simulate task completion
            with tasks.lock_tasks(self.test_tasks_file):
                data = tasks.load_tasks(self.test_tasks_file)
                data.tasks[0].status = "completed"
                tasks.save_tasks(self.test_tasks_file, data)
            return 0

        mock_process.wait.side_effect = wait_side_effect
        mock_popen.return_value = mock_process

        # 3. Run lemming
        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "retries", "1"]
        )
        result = self.cli_runner.invoke(
            main.cli, ["--verbose"] + self.base_args + ["run"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Attempt 1/1", result.output)
        self.assertIn("All tasks completed!", result.output)

        # Verify it was indeed picked up
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, "completed")


class TestLemmingLogging(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]
        os.environ["LEMMING_HOME"] = str(pathlib.Path(self.test_dir) / ".lemming")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_task_logging(self):
        # 1. Add a task
        res = self.cli_runner.invoke(main.cli, self.base_args + ["add", "Test logging"])
        self.assertEqual(res.exit_code, 0)
        task_id = res.output.strip()

        # 2. Run the task using a simple echo runner
        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "retries", "1"]
        )
        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "runner", "echo"]
        )
        res = self.cli_runner.invoke(
            main.cli,
            self.base_args
            + [
                "run",
                "Hello from log test",
            ],
        )

        log_file = paths.get_log_file(self.test_tasks_file, task_id)
        self.assertTrue(log_file.exists())
        content = log_file.read_text()
        self.assertIn("--- Attempt started at", content)
        self.assertIn("Hello from log test", content)

    def test_log_cleanup(self):
        # Test reset cleanup
        res = self.cli_runner.invoke(main.cli, self.base_args + ["add", "Test cleanup"])
        task_id = res.output.strip()
        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "retries", "1"]
        )
        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "runner", "echo"]
        )
        self.cli_runner.invoke(
            main.cli,
            self.base_args
            + [
                "run",
                "cleanup test",
            ],
        )

        log_file = paths.get_log_file(self.test_tasks_file, task_id)
        self.assertTrue(log_file.exists())

        self.cli_runner.invoke(main.cli, self.base_args + ["reset", task_id])
        self.assertFalse(log_file.exists())

        # Delete the first task so it doesn't get picked up again
        self.cli_runner.invoke(main.cli, self.base_args + ["delete", task_id])

        # Test delete cleanup
        res = self.cli_runner.invoke(
            main.cli, self.base_args + ["add", "Test delete cleanup"]
        )
        task_id_2 = res.output.strip()
        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "retries", "1"]
        )
        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "runner", "echo"]
        )
        self.cli_runner.invoke(
            main.cli,
            self.base_args
            + [
                "run",
                "delete test",
            ],
        )

        log_file_2 = paths.get_log_file(self.test_tasks_file, task_id_2)
        self.assertTrue(log_file_2.exists())

        self.cli_runner.invoke(main.cli, self.base_args + ["delete", task_id_2])
        self.assertFalse(log_file_2.exists())

    def test_delete_all_cleanup(self):
        # Add two tasks
        res1 = self.cli_runner.invoke(main.cli, self.base_args + ["add", "Task 1"])
        id1 = res1.output.strip()
        res2 = self.cli_runner.invoke(main.cli, self.base_args + ["add", "Task 2"])
        id2 = res2.output.strip()

        # Create logs manually to ensure they exist
        log1 = paths.get_log_file(self.test_tasks_file, id1)
        log2 = paths.get_log_file(self.test_tasks_file, id2)
        log1.write_text("log 1")
        log2.write_text("log 2")

        self.assertTrue(log1.exists())
        self.assertTrue(log2.exists())

        # Delete all
        self.cli_runner.invoke(main.cli, self.base_args + ["delete", "--all"])

        self.assertFalse(log1.exists())
        self.assertFalse(log2.exists())


class TestLemmingShare(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]
        self.original_share_token = getattr(api.app.state, "share_token", None)

    def tearDown(self):
        shutil.rmtree(self.test_dir)
        api.app.state.share_token = self.original_share_token

    def test_parse_timeout(self):
        self.assertEqual(main.parse_timeout("0"), 0.0)
        self.assertEqual(main.parse_timeout("-1h"), 0.0)
        self.assertEqual(main.parse_timeout("8h"), 8 * 3600.0)
        self.assertEqual(main.parse_timeout("30m"), 30 * 60.0)
        self.assertEqual(main.parse_timeout("90s"), 90.0)
        self.assertEqual(main.parse_timeout("invalid"), 0.0)

    @unittest.mock.patch("uvicorn.run")
    @unittest.mock.patch("lemming.providers.CloudflareProvider")
    def test_share_cloudflare_command(self, mock_cf, mock_uvicorn):
        # Setup mock provider
        mock_provider = unittest.mock.MagicMock()
        mock_provider.start.return_value = "https://mock.trycloudflare.com"
        mock_cf.return_value = mock_provider

        # We override sleep so monitor thread exits instantly
        with (
            unittest.mock.patch("time.sleep", return_value=None),
            unittest.mock.patch("os._exit", side_effect=SystemExit),
        ):
            result = self.cli_runner.invoke(
                main.cli,
                self.base_args + ["serve", "--tunnel", "cloudflare", "--timeout", "0"],
            )

            self.assertEqual(result.exit_code, 0)
            self.assertIn("Initiating public tunnel via Cloudflare", result.output)
            self.assertIn("https://mock.trycloudflare.com?token=", result.output)
            mock_provider.start.assert_called_once_with(8999)
            mock_uvicorn.assert_called_once()
            mock_provider.stop.assert_called_once()

    @unittest.mock.patch("uvicorn.run")
    @unittest.mock.patch("lemming.providers.TailscaleProvider")
    def test_share_tailscale_command(self, mock_ts, mock_uvicorn):
        mock_provider = unittest.mock.MagicMock()
        mock_provider.start.return_value = "https://mock.ts.net"
        mock_ts.return_value = mock_provider

        with (
            unittest.mock.patch("time.sleep", return_value=None),
            unittest.mock.patch("os._exit", side_effect=SystemExit),
        ):
            result = self.cli_runner.invoke(
                main.cli,
                self.base_args + ["serve", "--tunnel", "tailscale", "--timeout", "0"],
            )

            self.assertEqual(result.exit_code, 0)
            self.assertIn("Initiating public tunnel via Tailscale", result.output)
            self.assertIn("https://mock.ts.net?token=", result.output)
            mock_provider.start.assert_called_once_with(8999)
            mock_uvicorn.assert_called_once()
            mock_provider.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
