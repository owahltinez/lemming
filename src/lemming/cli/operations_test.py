import logging.config
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import click.testing

from lemming import models, paths, persistence
from lemming.api import main as api_main
from lemming.cli import main as cli
from lemming.cli import operations


class TestCLIOperations(unittest.TestCase):
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
            tasks=[],
        )
        persistence.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_run_help(self):
        result = self.cli_runner.invoke(cli.cli, ["run", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Starts the orchestrator loop", result.output)

    def test_run_empty_queue_reports_completion(self):
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["run"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("All tasks completed!", result.output)

    def test_run_blocked_queue_exits_nonzero(self):
        persistence.save_tasks(
            self.test_tasks_file,
            models.Roadmap(
                tasks=[
                    models.Task(
                        id="active123",
                        description="Active task",
                        status=models.TaskStatus.IN_PROGRESS,
                        pid=1234,
                        last_heartbeat=time.time(),
                    ),
                    models.Task(
                        id="pending456",
                        description="Pending task",
                        status=models.TaskStatus.PENDING,
                    ),
                ]
            ),
        )

        with mock.patch(
            "lemming.tasks.lifecycle.is_pid_alive", return_value=True
        ):
            result = self.cli_runner.invoke(cli.cli, self.base_args + ["run"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Queue blocked by active task active123", result.output)
        self.assertIn("1 pending task remains", result.output)
        self.assertNotIn("All tasks completed!", result.output)

    def test_run_reclaims_task_abandoned_by_dead_loop(self):
        persistence.save_tasks(
            self.test_tasks_file,
            models.Roadmap(
                tasks=[
                    models.Task(
                        id="orphan12",
                        description="Orphaned task",
                        status=models.TaskStatus.IN_PROGRESS,
                        attempts=1,
                        pid=999999,
                        last_heartbeat=time.time() - 3600,
                    )
                ],
                config=models.RoadmapConfig(retries=2, runner="true", hooks=[]),
            ),
        )

        with mock.patch("time.sleep"):
            result = self.cli_runner.invoke(
                cli.cli, ["--tasks-file", str(self.test_tasks_file), "run"]
            )

        self.assertIn("Task orphan12 was cut off", result.output)
        # The cut-off attempt is rerun, not charged; the next retry still is.
        self.assertIn("[orphan12] Attempt 1/2", result.output)
        self.assertIn("[orphan12] Attempt 2/2", result.output)
        task = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertEqual(task.attempts, 2)

    def test_interrupted_run_rewinds_its_task_on_the_way_out(self):
        work = pathlib.Path(self.test_dir)
        (work / "app.py").write_text("v1\n")
        persistence.save_tasks(
            self.test_tasks_file,
            models.Roadmap(
                tasks=[models.Task(id="t1", description="Interrupted")],
                config=models.RoadmapConfig(retries=2, runner="true", hooks=[]),
            ),
        )

        def interrupt(cmd, tasks_file, task_id, *args, **kwargs):
            (work / "app.py").write_text("partial\n")
            raise KeyboardInterrupt

        with mock.patch("lemming.runner.run_with_heartbeat", interrupt):
            result = self.cli_runner.invoke(
                cli.cli,
                ["--tasks-file", str(self.test_tasks_file), "-C", str(work)]
                + ["run"],
            )

        self.assertNotEqual(result.exit_code, 0)
        # Anything acting on the task before the next run sees a clean slate.
        self.assertEqual((work / "app.py").read_text(), "v1\n")
        task = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertEqual(task.status, models.TaskStatus.PENDING)
        self.assertEqual(task.attempts, 0)
        self.assertIsNone(task.pid)
        self.assertIsNone(task.last_started_at)

    def test_run_reclaims_task_abandoned_on_its_last_attempt(self):
        persistence.save_tasks(
            self.test_tasks_file,
            models.Roadmap(
                tasks=[
                    models.Task(
                        id="orphan12",
                        description="Orphaned task",
                        status=models.TaskStatus.IN_PROGRESS,
                        attempts=2,
                        pid=999999,
                        last_heartbeat=time.time() - 3600,
                    )
                ],
                config=models.RoadmapConfig(retries=2, runner="true", hooks=[]),
            ),
        )

        with mock.patch("time.sleep"):
            result = self.cli_runner.invoke(
                cli.cli, ["--tasks-file", str(self.test_tasks_file), "run"]
            )

        self.assertIn("Task orphan12 was cut off", result.output)
        self.assertIn("[orphan12] Attempt 2/2", result.output)
        task = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertEqual(task.attempts, 2)

    def test_run_completes_task_abandoned_during_completion_hooks(self):
        persistence.save_tasks(
            self.test_tasks_file,
            models.Roadmap(
                tasks=[
                    models.Task(
                        id="orphan12",
                        description="Orphaned task",
                        status=models.TaskStatus.IN_PROGRESS,
                        requested_status=models.TaskStatus.COMPLETED,
                        attempts=1,
                        pid=999999,
                        last_heartbeat=time.time() - 3600,
                    )
                ],
                config=models.RoadmapConfig(retries=1, runner="true", hooks=[]),
            ),
        )

        with mock.patch("time.sleep"):
            result = self.cli_runner.invoke(
                cli.cli, ["--tasks-file", str(self.test_tasks_file), "run"]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        task = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertEqual(task.status, models.TaskStatus.COMPLETED)

    def test_run_fails_task_abandoned_during_failure_hooks(self):
        persistence.save_tasks(
            self.test_tasks_file,
            models.Roadmap(
                tasks=[
                    models.Task(
                        id="orphan12",
                        description="Orphaned task",
                        status=models.TaskStatus.IN_PROGRESS,
                        requested_status=models.TaskStatus.FAILED,
                        attempts=1,
                        pid=999999,
                        last_heartbeat=time.time() - 3600,
                    ),
                    models.Task(id="next3456", description="Dependent task"),
                ],
                config=models.RoadmapConfig(retries=1, runner="true", hooks=[]),
            ),
        )

        with mock.patch("time.sleep"):
            result = self.cli_runner.invoke(
                cli.cli, ["--tasks-file", str(self.test_tasks_file), "run"]
            )

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("failed after 1 attempts. Aborting run.", result.output)
        tasks = persistence.load_tasks(self.test_tasks_file).tasks
        self.assertEqual(tasks[0].status, models.TaskStatus.FAILED)
        self.assertEqual(tasks[1].status, models.TaskStatus.PENDING)

    def test_run_detach_spawns_loop_and_returns(self):
        persistence.save_tasks(
            self.test_tasks_file,
            models.Roadmap(
                tasks=[models.Task(id="abcd1234", description="Milestone")]
            ),
        )

        with (
            tempfile.TemporaryDirectory() as repo,
            mock.patch.dict(os.environ),
            mock.patch("subprocess.Popen") as popen,
            mock.patch.object(operations, "run_loop") as run_loop,
        ):
            popen.return_value.pid = 4242
            result = self.cli_runner.invoke(
                cli.cli,
                ["-C", repo]
                + self.base_args
                + [
                    "run",
                    "--detach",
                    "--until",
                    "abcd",
                    "--max-tasks",
                    "2",
                    "--env",
                    "LEMMING_TEST_VAR=1",
                    "--",
                    "--model",
                    "m1",
                ],
            )
            repo_dir = str(pathlib.Path(repo).resolve())

        self.assertEqual(result.exit_code, 0, result.output)
        run_loop.assert_not_called()
        log_file = paths.get_project_dir(self.test_tasks_file) / "loop.log"
        self.assertIn("4242", result.output)
        self.assertIn(str(log_file), result.output)
        cmd = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        global_args = cmd[: cmd.index("run")]
        self.assertEqual(global_args[global_args.index("-C") + 1], repo_dir)
        self.assertEqual(
            global_args[global_args.index("--tasks-file") + 1],
            str(self.test_tasks_file.resolve()),
        )
        run_args = cmd[cmd.index("run") + 1 :]
        self.assertNotIn("--detach", run_args)
        self.assertIn("--until=abcd1234", run_args)
        self.assertIn("--max-tasks=2", run_args)
        self.assertEqual(
            run_args[run_args.index("--") + 1 :], ["--model", "m1"]
        )
        self.assertEqual(kwargs["env"]["LEMMING_TEST_VAR"], "1")
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"].name, str(log_file))

    def test_run_detach_without_project_dir_omits_it(self):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value.pid = 4242
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["run", "--detach"]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("-C", popen.call_args.args[0])

    def test_run_detach_rotates_oversized_log(self):
        log_file = paths.get_project_dir(self.test_tasks_file) / "loop.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        old = "x" * (operations.LOOP_LOG_MAX_BYTES + 1)
        log_file.write_text(old)
        log_file.with_name("loop.log.1").write_text("older")

        with mock.patch("subprocess.Popen") as popen:
            popen.return_value.pid = 4242
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["run", "--detach"]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(log_file.read_text(), "")
        self.assertEqual(log_file.with_name("loop.log.1").read_text(), old)
        self.assertEqual(popen.call_args.kwargs["stdout"].name, str(log_file))

    def test_run_detach_appends_to_small_log(self):
        log_file = paths.get_project_dir(self.test_tasks_file) / "loop.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("previous run\n")

        with mock.patch("subprocess.Popen") as popen:
            popen.return_value.pid = 4242
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["run", "--detach"]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(log_file.read_text(), "previous run\n")
        self.assertFalse(log_file.with_name("loop.log.1").exists())

    def test_run_refuses_to_reclaim_task_with_live_runner(self):
        runner = subprocess.Popen(["sleep", "60"])
        try:
            persistence.save_tasks(
                self.test_tasks_file,
                models.Roadmap(
                    tasks=[
                        models.Task(
                            id="orphan12",
                            description="Orphaned task",
                            status=models.TaskStatus.IN_PROGRESS,
                            attempts=1,
                            pid=runner.pid,
                            last_heartbeat=time.time() - 3600,
                        )
                    ],
                    config=models.RoadmapConfig(
                        retries=2, runner="true", hooks=[]
                    ),
                ),
            )

            with mock.patch("time.sleep"):
                result = self.cli_runner.invoke(
                    cli.cli, ["--tasks-file", str(self.test_tasks_file), "run"]
                )
        finally:
            runner.kill()
            runner.wait()

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn(f"pid {runner.pid}", result.output)
        self.assertNotIn("Reclaiming", result.output)
        task = persistence.load_tasks(self.test_tasks_file).tasks[0]
        self.assertEqual(task.attempts, 1)
        self.assertEqual(task.pid, runner.pid)

    def test_run_detach_refuses_when_loop_is_running(self):
        persistence.acquire_loop_lock(self.test_tasks_file)
        try:
            with mock.patch("lemming.api.loop.spawn_loop") as spawn_loop:
                result = self.cli_runner.invoke(
                    cli.cli, self.base_args + ["run", "--detach"]
                )
        finally:
            persistence.release_loop_lock(self.test_tasks_file)

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Another loop is already running", result.output)
        spawn_loop.assert_not_called()

    def test_run_rejects_live_loop_owner(self):
        persistence.acquire_loop_lock(self.test_tasks_file)
        try:
            result = self.cli_runner.invoke(cli.cli, self.base_args + ["run"])
        finally:
            persistence.release_loop_lock(self.test_tasks_file)

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Another loop is already running", result.output)

    def test_run_forwards_max_tasks(self):
        with mock.patch.object(
            operations, "run_loop", return_value=True
        ) as run_loop:
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["run", "--max-tasks", "2"]
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(run_loop.call_args.kwargs["max_tasks"], 2)

    def test_run_uses_project_dir_over_external_tasks_file(self):
        with tempfile.TemporaryDirectory() as repo:
            repo_dir = pathlib.Path(repo).resolve()
            with mock.patch.object(
                operations, "run_loop", return_value=True
            ) as run_loop:
                result = self.cli_runner.invoke(
                    cli.cli, ["-C", repo] + self.base_args + ["run"]
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(run_loop.call_args.kwargs["working_dir"], repo_dir)

    def test_run_applies_env_before_resolving_working_dir(self):
        # With LEMMING_HOME pointing at the tasks file's dir, the file is
        # isolated state and the runner must stay in the current directory.
        with (
            mock.patch.dict(os.environ),
            mock.patch.object(
                operations, "run_loop", return_value=True
            ) as run_loop,
        ):
            result = self.cli_runner.invoke(
                cli.cli,
                self.base_args
                + ["run", "--env", f"LEMMING_HOME={self.test_dir}"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            run_loop.call_args.kwargs["working_dir"],
            pathlib.Path.cwd().resolve(),
        )

    def test_run_resolves_until_prefix(self):
        persistence.save_tasks(
            self.test_tasks_file,
            models.Roadmap(
                tasks=[models.Task(id="abcd1234", description="Milestone")]
            ),
        )

        with mock.patch.object(
            operations, "run_loop", return_value=True
        ) as run_loop:
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["run", "--until", "abcd"]
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(run_loop.call_args.kwargs["until"], "abcd1234")

    def test_run_rejects_unknown_until(self):
        with mock.patch.object(operations, "run_loop") as run_loop:
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["run", "--until", "nope"]
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Task nope not found", result.output)
        run_loop.assert_not_called()

    def test_run_rejects_zero_max_tasks(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["run", "--max-tasks", "0"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not in the range", result.output)

    def test_run_rejects_a_stray_argument(self):
        """A bare word must not reach the agent CLI."""
        with mock.patch.object(operations, "run_loop") as run_loop:
            result = self.cli_runner.invoke(
                cli.cli, self.base_args + ["run", "stray"]
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("unexpected extra argument (stray)", result.output)
        run_loop.assert_not_called()

    def test_run_passes_arguments_after_a_separator_to_the_runner(self):
        with mock.patch.object(
            operations, "run_loop", return_value=True
        ) as run_loop:
            result = self.cli_runner.invoke(
                cli.cli,
                self.base_args + ["run", "--", "--model", "--", "x"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(run_loop.call_args.args[5], ("--model", "--", "x"))

    def test_serve_help(self):
        result = self.cli_runner.invoke(cli.cli, ["serve", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Launches the local web dashboard", result.output)

    def test_serve_non_loopback_enables_share_token(self):
        original_token = getattr(api_main.app.state, "share_token", None)
        try:
            with mock.patch("uvicorn.run"):
                result = self.cli_runner.invoke(
                    cli.cli,
                    self.base_args + ["serve", "--host", "0.0.0.0"],
                )
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(api_main.app.state.share_token)
            self.assertIn(
                f"?token={api_main.app.state.share_token}", result.output
            )
        finally:
            api_main.app.state.share_token = original_token


class TestQuietPollLogConfig(unittest.TestCase):
    def test_filter_path_resolves(self):
        """`serve` names the filter by string; nothing else resolves it."""
        log_config = operations.quiet_poll_log_config()
        factory = log_config["filters"]["quiet_poll"]["()"]

        # resolve() is what dictConfig calls, minus the global side effects.
        logging.config.BaseConfigurator({}).resolve(factory)


if __name__ == "__main__":
    unittest.main()
