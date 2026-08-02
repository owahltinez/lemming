import os
import pathlib
import shutil
import tempfile
import time
import unittest
from unittest import mock

import click.testing

from lemming import cli, paths, tasks


class TestCLITasks(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = [
            "--verbose",
            "--tasks-file",
            str(self.test_tasks_file),
        ]

        # Scaffold a valid file
        data = tasks.Roadmap(
            goal="Initial goal",
            tasks=[
                tasks.Task(
                    id="12345678",
                    description="Initial Task",
                    status=tasks.TaskStatus.PENDING,
                    attempts=0,
                    progress=[],
                )
            ],
        )
        tasks.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_add_task(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["add", "New Task"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Added task", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        task_descs = [t.description for t in data.tasks]
        self.assertIn("New Task", task_descs)

    def test_add_task_reports_actionable_size_error(self):
        result = self.cli_runner.invoke(
            cli.cli,
            self.base_args
            + ["add", "x" * (tasks.MAX_TASK_DESCRIPTION_CHARS + 1)],
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("2,001 characters (limit 2,000)", result.output)
        self.assertIn("Keep the brief task-specific", result.output)
        self.assertEqual(len(tasks.load_tasks(self.test_tasks_file).tasks), 1)

    def test_edit_task_description(self):
        result = self.cli_runner.invoke(
            cli.cli,
            self.base_args
            + ["edit", "12345678", "--description", "Updated Task"],
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task 12345678 updated.", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].description, "Updated Task")

    def test_delete_task(self):
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["add", "To be removed"]
        )

        data = tasks.load_tasks(self.test_tasks_file)
        task_id = next(
            t.id for t in data.tasks if t.description == "To be removed"
        )

        with mock.patch.dict(
            os.environ,
            {"LEMMING_HOME": str(pathlib.Path(self.test_dir) / "home")},
        ):
            log_file = paths.get_log_file(self.test_tasks_file, task_id)
            log_file.write_text("retained runner output")

            delete_result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["delete", task_id, "--force"]
            )
            self.assertEqual(delete_result.exit_code, 0)
            self.assertIn("runner log retained", delete_result.output)
            self.assertTrue(log_file.exists())

            logs_result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["logs", task_id[:4]]
            )
            self.assertEqual(logs_result.exit_code, 0)
            self.assertIn("retained runner output", logs_result.output)

            status_result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["status", task_id[:4]]
            )
            self.assertEqual(status_result.exit_code, 0)
            self.assertIn(f"Task {task_id} was removed", status_result.output)
            self.assertIn(str(log_file), status_result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        task_descs = [t.description for t in data.tasks]
        self.assertNotIn("To be removed", task_descs)

    def test_started_task_must_be_superseded_or_force_deleted(self):
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks[0].attempts = 1
            tasks.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["delete", "12345678"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Supersede it", result.output)

    def test_supersede_command_preserves_task_and_shows_replacements(self):
        child = tasks.add_task(
            self.test_tasks_file,
            "Smaller replacement",
            parent="12345678",
        )

        result = self.cli_runner.invoke(
            cli.cli,
            self.base_args
            + [
                "supersede",
                "1234",
                "--reason",
                "split after reaching the time limit",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        task = next(
            item
            for item in tasks.load_tasks(self.test_tasks_file).tasks
            if item.id == "12345678"
        )
        self.assertEqual(task.status, tasks.TaskStatus.SUPERSEDED)

        status = self.cli_runner.invoke(
            cli.cli, self.base_args + ["status", "1234"]
        )
        self.assertIn("Status:        superseded", status.output)
        self.assertIn(
            "Reason:         split after reaching the time limit",
            status.output,
        )
        self.assertIn("Replaced By:", status.output)
        self.assertIn(
            f"{child.id} [pending] Smaller replacement",
            status.output,
        )

    def test_status_overview_separates_queue_and_visible_history(self):
        child = tasks.add_task(
            self.test_tasks_file,
            "Smaller replacement",
            parent="12345678",
        )
        tasks.supersede_task(
            self.test_tasks_file,
            "12345678",
            "split after timeout",
        )

        result = self.cli_runner.invoke(
            cli.cli,
            [
                "--tasks-file",
                str(self.test_tasks_file),
                "status",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Queue:", result.output)
        self.assertIn(f"[ ] ({child.id}) [parent:12345678]", result.output)
        self.assertIn("History:", result.output)
        self.assertIn("[~] (12345678) Initial Task", result.output)
        self.assertIn("Reason: split after timeout", result.output)
        self.assertIn(f"Replaced by: {child.id}", result.output)

    def test_status_command(self):
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["status"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Loop Status: Idle", result.output)
        self.assertIn("Initial Task", result.output)

        # Verify Running state (mocking is_loop_running)
        with mock.patch(
            "lemming.tasks.lifecycle.is_loop_running", return_value=True
        ):
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["status"]
            )
            self.assertIn("Loop Status: Running", result.output)

    def test_status_command_shows_execution_time_breakdown(self):
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks[0].run_time = 1422
            data.tasks[0].execution_times = {
                "hook:testing": 216,
                "runner": 894,
                "hook:readability": 282,
                "hook:ux": 12,
                "hook:roadmap": 12,
            }
            tasks.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["status", "1234"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Run Time:      23m 42s", result.output)
        runner_index = result.output.index("  runner       14m 54s")
        readability_index = result.output.index("  readability  4m 42s")
        testing_index = result.output.index("  testing      3m 36s")
        ux_index = result.output.index("  ux           12.0s")
        roadmap_index = result.output.index("  roadmap      12.0s")
        self.assertLess(runner_index, testing_index)
        self.assertLess(testing_index, readability_index)
        self.assertLess(readability_index, ux_index)
        self.assertLess(ux_index, roadmap_index)

    def test_logs_command_fail_no_logs(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["logs", "12345678"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No log for task", result.output)

    def test_task_complete(self):
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["progress", "12345678", "Done"]
        )
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["complete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.COMPLETED)

    def test_active_task_completion_runs_hooks(self):
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks[0].status = tasks.TaskStatus.IN_PROGRESS
            data.tasks[0].progress = ["Done"]
            data.tasks[0].pid = 1234
            data.tasks[0].last_heartbeat = time.time()
            tasks.save_tasks(self.test_tasks_file, data)

        with mock.patch(
            "lemming.tasks.lifecycle.is_pid_alive", return_value=True
        ):
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["complete", "12345678"]
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("completion requested", result.output)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.IN_PROGRESS)
        self.assertEqual(
            data.tasks[0].requested_status, tasks.TaskStatus.COMPLETED
        )

    def test_stale_task_completion_requires_force(self):
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks[0].status = tasks.TaskStatus.IN_PROGRESS
            data.tasks[0].progress = ["Done"]
            data.tasks[0].pid = 999999
            data.tasks[0].last_heartbeat = 0
            tasks.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["complete", "12345678"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Use --force", result.output)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.IN_PROGRESS)
        self.assertIsNone(data.tasks[0].requested_status)

    def test_force_completes_stale_task(self):
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks[0].status = tasks.TaskStatus.IN_PROGRESS
            data.tasks[0].progress = ["Done"]
            data.tasks[0].pid = 999999
            data.tasks[0].last_heartbeat = 0
            tasks.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["complete", "--force", "12345678"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("marked as completed", result.output)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.COMPLETED)
        self.assertIsNone(data.tasks[0].requested_status)
        self.assertIsNone(data.tasks[0].pid)
        self.assertIsNone(data.tasks[0].last_heartbeat)

    def test_task_uncomplete(self):
        # First complete it
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["progress", "12345678", "Done"]
        )
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["complete", "12345678"]
        )

        # Then uncomplete
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["uncomplete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.PENDING)

    def test_task_fail(self):
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["progress", "12345678", "Failed reason"]
        )
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["fail", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.FAILED)

    def test_cancel_command(self):
        # We need a fake in-progress task for this
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks[0].status = tasks.TaskStatus.IN_PROGRESS
            tasks.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["cancel", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task 12345678 cancelled.", result.output)

    def test_reset_command(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["reset", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("attempts, progress, and logs cleared", result.output)


if __name__ == "__main__":
    unittest.main()


class TestCLITaskModel(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]
        tasks.save_tasks(self.test_tasks_file, tasks.Roadmap(goal="g"))

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _add(self, *extra):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["add", "do the thing", *extra]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        return tasks.load_tasks(self.test_tasks_file).tasks[0]

    def test_add_records_model(self):
        """Per-task model selection is a field, not a runner-string trick."""
        task = self._add("--model", "fast-model")

        self.assertEqual(task.model, "fast-model")

    def test_edit_changes_model(self):
        task = self._add("--model", "old-model")

        result = self.cli_runner.invoke(
            cli.cli,
            self.base_args + ["edit", task.id, "--model", "new-model"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        updated = tasks.load_tasks(self.test_tasks_file).tasks[0]
        self.assertEqual(updated.model, "new-model")

    def test_edit_empty_model_restores_project_default(self):
        task = self._add("--model", "old-model")

        self.cli_runner.invoke(
            cli.cli, self.base_args + ["edit", task.id, "--model", ""]
        )

        updated = tasks.load_tasks(self.test_tasks_file).tasks[0]
        self.assertIsNone(updated.model)

    def test_status_shows_provenance(self):
        """After the fact, status answers which command produced the work."""
        task = self._add("--model", "fast-model")
        tasks.record_resolved_command(
            self.test_tasks_file, task.id, "agy --model fast-model"
        )

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["status", task.id]
        )

        self.assertIn("Custom Model:", result.output)
        self.assertIn("agy --model fast-model", result.output)

    def test_runner_help_documents_extra_arguments(self):
        """The extra-args behaviour must be discoverable from --help."""
        result = self.cli_runner.invoke(cli.cli, ["add", "--help"])

        self.assertIn("--model", result.output)
        self.assertIn("{{prompt}}", result.output)
