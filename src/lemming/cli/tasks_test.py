import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import click.testing

from lemming import models, paths, persistence, snapshots
from lemming.cli import main as cli
from lemming.tasks import lifecycle, limits, operations


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
        data = models.Roadmap(
            goal="Initial goal",
            tasks=[
                models.Task(
                    id="12345678",
                    description="Initial Task",
                    status=models.TaskStatus.PENDING,
                    attempts=0,
                    progress=[],
                )
            ],
        )
        persistence.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_add_task(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["add", "New Task"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Added task", result.output)

        data = persistence.load_tasks(self.test_tasks_file)
        task_descs = [t.description for t in data.tasks]
        self.assertIn("New Task", task_descs)

    def test_add_task_reports_actionable_size_error(self):
        result = self.cli_runner.invoke(
            cli.cli,
            self.base_args
            + ["add", "x" * (limits.MAX_TASK_DESCRIPTION_CHARS + 1)],
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("2,001 characters (limit 2,000)", result.output)
        self.assertIn("Keep the description task-specific", result.output)
        self.assertIn("lemming brief", result.output)
        self.assertEqual(
            len(persistence.load_tasks(self.test_tasks_file).tasks), 1
        )

    def test_edit_task_description(self):
        result = self.cli_runner.invoke(
            cli.cli,
            self.base_args
            + ["edit", "12345678", "--description", "Updated Task"],
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task 12345678 updated.", result.output)

        data = persistence.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].description, "Updated Task")

    def test_delete_task(self):
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["add", "To be removed"]
        )

        data = persistence.load_tasks(self.test_tasks_file)
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

        data = persistence.load_tasks(self.test_tasks_file)
        task_descs = [t.description for t in data.tasks]
        self.assertNotIn("To be removed", task_descs)

    def test_started_task_must_be_superseded_or_force_deleted(self):
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].attempts = 1
            persistence.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["delete", "12345678"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Supersede it", result.output)

    def test_supersede_command_preserves_task_and_shows_replacements(self):
        child = operations.add_task(
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
            for item in persistence.load_tasks(self.test_tasks_file).tasks
            if item.id == "12345678"
        )
        self.assertEqual(task.status, models.TaskStatus.SUPERSEDED)

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
        child = operations.add_task(
            self.test_tasks_file,
            "Smaller replacement",
            parent="12345678",
        )
        operations.supersede_task(
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

    def test_status_flags_task_abandoned_by_dead_loop(self):
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].status = models.TaskStatus.IN_PROGRESS
            data.tasks[0].pid = 999999
            data.tasks[0].last_heartbeat = 0
            persistence.save_tasks(self.test_tasks_file, data)

        overview = self.cli_runner.invoke(
            cli.cli, self.base_args + ["status", "--brief"]
        )
        detail = self.cli_runner.invoke(
            cli.cli, self.base_args + ["status", "12345678"]
        )

        for result in (overview, detail):
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("abandoned", result.output.lower())
            self.assertIn("lemming run --detach", result.output)

    def test_status_names_runner_that_outlived_its_loop(self):
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].status = models.TaskStatus.IN_PROGRESS
            data.tasks[0].pid = os.getpid()
            data.tasks[0].last_heartbeat = 0
            persistence.save_tasks(self.test_tasks_file, data)

        overview = self.cli_runner.invoke(
            cli.cli, self.base_args + ["status", "--brief"]
        )
        detail = self.cli_runner.invoke(
            cli.cli, self.base_args + ["status", "12345678"]
        )

        for result in (overview, detail):
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn(f"runner pid {os.getpid()}", result.output)
            self.assertNotIn("lemming run", result.output)

    def test_status_does_not_flag_live_task(self):
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].status = models.TaskStatus.IN_PROGRESS
            data.tasks[0].pid = 1234
            data.tasks[0].last_heartbeat = time.time()
            persistence.save_tasks(self.test_tasks_file, data)

        with mock.patch(
            "lemming.tasks.lifecycle.is_pid_alive", return_value=True
        ):
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["status"]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("abandoned", result.output.lower())

    def test_status_command_shows_execution_time_breakdown(self):
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].run_time = 1422
            data.tasks[0].execution_times = {
                "hook:testing": 216,
                "runner": 894,
                "hook:readability": 282,
                "hook:ux": 12,
                "hook:roadmap": 12,
            }
            persistence.save_tasks(self.test_tasks_file, data)

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

    def test_logs_prints_non_utf8_argv_as_raw_bytes(self):
        log_file = paths.get_log_file(self.test_tasks_file, "12345678")
        log_file.write_bytes(b"Command: true caf\xe9\n")

        # A real process: CliRunner's streams are strict regardless of main().
        result = subprocess.run(
            [sys.executable, "-m", "lemming.main", *self.base_args]
            + ["logs", "12345678"],
            capture_output=True,
            env={**os.environ, "HOME": self.test_dir},
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b"Command: true caf\xe9\n", result.stdout)

    def test_task_complete(self):
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["progress", "12345678", "Done"]
        )
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["complete", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)

        data = persistence.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, models.TaskStatus.COMPLETED)

    def test_active_task_completion_runs_hooks(self):
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].status = models.TaskStatus.IN_PROGRESS
            data.tasks[0].progress = ["Done"]
            data.tasks[0].pid = 1234
            data.tasks[0].last_heartbeat = time.time()
            persistence.save_tasks(self.test_tasks_file, data)

        with mock.patch(
            "lemming.tasks.lifecycle.is_pid_alive", return_value=True
        ):
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["complete", "12345678"]
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("completion requested", result.output)
        data = persistence.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, models.TaskStatus.IN_PROGRESS)
        self.assertEqual(
            data.tasks[0].requested_status, models.TaskStatus.COMPLETED
        )

    def test_stale_task_completion_requires_force(self):
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].status = models.TaskStatus.IN_PROGRESS
            data.tasks[0].progress = ["Done"]
            data.tasks[0].pid = 999999
            data.tasks[0].last_heartbeat = 0
            persistence.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["complete", "12345678"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Use --force", result.output)
        data = persistence.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, models.TaskStatus.IN_PROGRESS)
        self.assertIsNone(data.tasks[0].requested_status)

    def test_force_completes_stale_task(self):
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].status = models.TaskStatus.IN_PROGRESS
            data.tasks[0].progress = ["Done"]
            data.tasks[0].pid = 999999
            data.tasks[0].last_heartbeat = 0
            persistence.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["complete", "--force", "12345678"]
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("marked as completed", result.output)
        data = persistence.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, models.TaskStatus.COMPLETED)
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
        data = persistence.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, models.TaskStatus.PENDING)

    def test_task_fail(self):
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["progress", "12345678", "Failed reason"]
        )
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["fail", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        data = persistence.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, models.TaskStatus.FAILED)

    def test_cancel_command(self):
        # We need a fake in-progress task for this
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].status = models.TaskStatus.IN_PROGRESS
            persistence.save_tasks(self.test_tasks_file, data)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["cancel", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Task 12345678 cancelled.", result.output)

        with mock.patch(
            "lemming.tasks.lifecycle.cancel_task", return_value=True
        ) as mock_cancel:
            res = self.cli_runner.invoke(
                cli.cli, self.base_args + ["cancel", "12345678"]
            )
            self.assertEqual(res.exit_code, 0)
            mock_cancel.assert_called_once_with(
                self.test_tasks_file.resolve(),
                "12345678",
                working_dir=None,
            )

    def test_reset_command(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["reset", "12345678"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("attempts, progress, and logs cleared", result.output)

    def test_status_displays_diff_summary(self):
        summary = "1 file (+2/-1): a.py (+2/-1)"
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].status = models.TaskStatus.COMPLETED
            data.tasks[0].diff_summary = summary
            persistence.save_tasks(self.test_tasks_file, data)

        overview = self.cli_runner.invoke(cli.cli, self.base_args + ["status"])
        self.assertEqual(overview.exit_code, 0)
        self.assertIn(f"    Changes: {summary}", overview.output)

        detail = self.cli_runner.invoke(
            cli.cli, self.base_args + ["status", "1234"]
        )
        self.assertEqual(detail.exit_code, 0)
        self.assertIn(f"Changes:       {summary}", detail.output)

    def test_diff_and_rollback_commands(self):
        work = pathlib.Path(self.test_dir)
        with mock.patch.dict(os.environ, {"LEMMING_HOME": str(work / "home")}):
            target_file = work / "hello.py"
            target_file.write_text("v1\n", encoding="utf-8")
            pre_tree = snapshots.snapshot_tree(self.test_tasks_file, work)

            target_file.write_text("v2\n", encoding="utf-8")
            runner_tree = snapshots.snapshot_tree(self.test_tasks_file, work)

            target_file.write_text("v2\n# hook\n", encoding="utf-8")
            post_tree = snapshots.snapshot_tree(self.test_tasks_file, work)
            assert pre_tree and runner_tree and post_tree

            with persistence.lock_tasks(self.test_tasks_file):
                data = persistence.load_tasks(self.test_tasks_file)
                data.tasks[0].status = models.TaskStatus.COMPLETED
                data.tasks[0].completed_at = 100.0
                data.tasks[0].pre_tree = pre_tree
                data.tasks[0].runner_tree = runner_tree
                data.tasks[0].post_tree = post_tree
                data.tasks[0].hook_trees = {
                    "readability": [runner_tree, post_tree]
                }
                persistence.save_tasks(self.test_tasks_file, data)

            diff_res = self.cli_runner.invoke(
                cli.cli, self.base_args + ["diff"]
            )
            self.assertEqual(diff_res.exit_code, 0, diff_res.output)
            self.assertIn("-v1", diff_res.output)
            self.assertIn("+v2", diff_res.output)

            hook_diff = self.cli_runner.invoke(
                cli.cli, self.base_args + ["diff", "--hook", "readability"]
            )
            self.assertEqual(hook_diff.exit_code, 0, hook_diff.output)
            self.assertNotIn("-v1", hook_diff.output)
            self.assertIn("+# hook", hook_diff.output)

            stat_res = self.cli_runner.invoke(
                cli.cli, self.base_args + ["diff", "1234", "--stat"]
            )
            self.assertEqual(stat_res.exit_code, 0, stat_res.output)
            self.assertIn("hello.py", stat_res.output)

            hook_rb = self.cli_runner.invoke(
                cli.cli,
                self.base_args + ["rollback", "1234", "--hook", "readability"],
            )
            self.assertEqual(hook_rb.exit_code, 0, hook_rb.output)
            self.assertEqual(target_file.read_text(encoding="utf-8"), "v2\n")
            after_hook = persistence.load_tasks(self.test_tasks_file).tasks[0]
            self.assertEqual(after_hook.status, models.TaskStatus.COMPLETED)
            self.assertIsNone(after_hook.hook_trees)

            rb_res = self.cli_runner.invoke(
                cli.cli, self.base_args + ["rollback", "1234", "--only"]
            )
            self.assertEqual(rb_res.exit_code, 0, rb_res.output)
            self.assertIn("12345678", rb_res.output)
            self.assertEqual(target_file.read_text(encoding="utf-8"), "v1\n")
            reloaded = persistence.load_tasks(self.test_tasks_file).tasks[0]
            self.assertEqual(reloaded.status, models.TaskStatus.PENDING)

    def test_diff_prints_non_utf8_content_as_raw_bytes(self):
        work = pathlib.Path(self.test_dir)
        env = {**os.environ, "LEMMING_HOME": str(work / "home")}
        env["HOME"] = str(work)
        with mock.patch.dict(os.environ, env):
            pre_tree = snapshots.snapshot_tree(self.test_tasks_file, work)
            (work / "notes.txt").write_bytes(b"caf\xe9\n")
            post_tree = snapshots.snapshot_tree(self.test_tasks_file, work)
        with persistence.lock_tasks(self.test_tasks_file):
            data = persistence.load_tasks(self.test_tasks_file)
            data.tasks[0].pre_tree = pre_tree
            data.tasks[0].post_tree = post_tree
            persistence.save_tasks(self.test_tasks_file, data)

        # A real process: CliRunner's streams are strict regardless of main().
        result = subprocess.run(
            [sys.executable, "-m", "lemming.main", *self.base_args]
            + ["diff", "1234"],
            capture_output=True,
            env=env,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b"+caf\xe9\n", result.stdout)

    def test_diff_does_not_snapshot_working_dir_for_cancelled_task(self):
        work = pathlib.Path(self.test_dir)
        with mock.patch.dict(os.environ, {"LEMMING_HOME": str(work / "home")}):
            target_file = work / "hello.py"
            target_file.write_text("v1\n", encoding="utf-8")
            pre_tree = snapshots.snapshot_tree(self.test_tasks_file, work)
            assert pre_tree is not None

            target_file.write_text("dirty later edit\n", encoding="utf-8")

            with persistence.lock_tasks(self.test_tasks_file):
                data = persistence.load_tasks(self.test_tasks_file)
                data.tasks[0].status = models.TaskStatus.CANCELLED
                data.tasks[0].completed_at = 100.0
                data.tasks[0].pre_tree = pre_tree
                data.tasks[0].runner_tree = None
                data.tasks[0].post_tree = None
                persistence.save_tasks(self.test_tasks_file, data)

            diff_res = self.cli_runner.invoke(
                cli.cli, self.base_args + ["diff", "1234"]
            )
            self.assertEqual(diff_res.exit_code, 0, diff_res.output)
            self.assertEqual(diff_res.output, "")

            combo_rb = self.cli_runner.invoke(
                cli.cli,
                self.base_args
                + ["rollback", "1234", "--only", "--hook", "readability"],
            )
            self.assertNotEqual(combo_rb.exit_code, 0)
            self.assertIn("Cannot combine --only and --hook", combo_rb.output)


if __name__ == "__main__":
    unittest.main()


class TestCLITaskModel(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]
        persistence.save_tasks(self.test_tasks_file, models.Roadmap(goal="g"))

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _add(self, *extra):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["add", "do the thing", *extra]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        return persistence.load_tasks(self.test_tasks_file).tasks[0]

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
        updated = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertEqual(updated.model, "new-model")

    def test_edit_empty_model_restores_project_default(self):
        task = self._add("--model", "old-model")

        self.cli_runner.invoke(
            cli.cli, self.base_args + ["edit", task.id, "--model", ""]
        )

        updated = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertIsNone(updated.model)

    def test_oneshot_round_trips_through_add_and_edit(self):
        """The flag must be settable and clearable from the CLI."""
        task = self._add("--oneshot")
        self.assertTrue(task.oneshot)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["edit", task.id, "--no-oneshot"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        updated = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertFalse(updated.oneshot)

    def test_hooks_round_trip_through_add_and_edit(self):
        task = self._add("--hooks", "ux,readability")
        self.assertEqual(task.hooks, ["readability", "ux"])

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["edit", task.id, "--hooks", ""]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        updated = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertEqual(updated.hooks, [])

    def test_edit_project_hooks_restores_project_default(self):
        task = self._add("--hooks", "ux")

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["edit", task.id, "--project-hooks"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        updated = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertIsNone(updated.hooks)

    def test_edit_rejects_hooks_with_project_hooks(self):
        task = self._add("--hooks", "ux")

        result = self.cli_runner.invoke(
            cli.cli,
            self.base_args
            + ["edit", task.id, "--hooks", "", "--project-hooks"],
        )

        self.assertEqual(result.exit_code, 1, result.output)
        updated = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertEqual(updated.hooks, ["ux"])

    def test_add_without_hooks_uses_project_default(self):
        self.assertIsNone(self._add().hooks)

    def test_hooks_rejects_unknown_and_orchestration_names(self):
        for value, message in (
            ("nope", "Unknown review 'nope'"),
            ("roadmap", "orchestrates the roadmap"),
        ):
            for args in (
                ["add", "task", "--hooks", value],
                ["edit", "12345678", "--hooks", value],
            ):
                result = self.cli_runner.invoke(cli.cli, self.base_args + args)
                self.assertEqual(result.exit_code, 1, result.output)
                self.assertIn(message, result.output)
        self.assertEqual(persistence.load_tasks(self.test_tasks_file).tasks, [])

    def test_status_shows_provenance(self):
        """After the fact, status answers which command produced the work."""
        task = self._add("--model", "fast-model")
        lifecycle.record_resolved_command(
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
