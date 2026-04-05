import pathlib
import shutil
import tempfile
import time
import unittest
import unittest.mock

from lemming import tasks
from lemming.orchestrator import format_duration, run_loop, parse_timeout
from lemming.runner import RETURNCODE_TIMEOUT


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"

        # Scaffold a valid file with one task
        self.initial_data = tasks.Roadmap(
            context="Initial context",
            tasks=[
                tasks.Task(
                    id="task1",
                    description="Task 1",
                    status=tasks.TaskStatus.PENDING,
                    attempts=0,
                    outcomes=[],
                )
            ],
            config=tasks.RoadmapConfig(retries=3, runner="gemini"),
        )
        tasks.save_tasks(self.test_tasks_file, self.initial_data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_parse_timeout(self):
        self.assertEqual(parse_timeout("0"), 0.0)
        self.assertEqual(parse_timeout("-1h"), 0.0)
        self.assertEqual(parse_timeout("8h"), 8 * 3600.0)
        self.assertEqual(parse_timeout("30m"), 30 * 60.0)
        self.assertEqual(parse_timeout("90s"), 90.0)
        self.assertEqual(parse_timeout("invalid"), 0.0)

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "none")
        self.assertEqual(format_duration(-1), "none")
        self.assertEqual(format_duration(30), "30m")
        self.assertEqual(format_duration(60), "1h")
        self.assertEqual(format_duration(90), "90m")
        self.assertEqual(format_duration(120), "2h")

    @unittest.mock.patch("subprocess.Popen")
    def test_run_loop_success(self, mock_popen):
        # Simulate runner reporting success
        mock_process = unittest.mock.MagicMock()
        mock_process.pid = 12345
        mock_process.poll.side_effect = [None, 0]
        mock_process.returncode = 0
        mock_process.stdout = iter(["stdout\n"])
        mock_process.communicate.return_value = ("stdout", "stderr")

        def wait_side_effect():
            with tasks.lock_tasks(self.test_tasks_file):
                data = tasks.load_tasks(self.test_tasks_file)
                data.tasks[0].status = tasks.TaskStatus.COMPLETED
                data.tasks[0].completed_at = time.time()
                tasks.save_tasks(self.test_tasks_file, data)
            return 0

        mock_process.wait.side_effect = wait_side_effect
        mock_popen.return_value = mock_process

        run_loop(
            self.test_tasks_file,
            verbose=True,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
        )

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.COMPLETED)

    @unittest.mock.patch("subprocess.Popen")
    @unittest.mock.patch("time.sleep", return_value=None)
    def test_run_loop_retry_and_fail(self, mock_sleep, mock_popen):
        # Runner finishes but doesn't report completion
        mock_process = unittest.mock.MagicMock()
        mock_process.pid = 12345
        mock_process.poll.return_value = 0
        mock_process.returncode = 0
        mock_process.stdout = iter(["stdout\n"])
        mock_process.communicate.return_value = ("stdout", "stderr")
        mock_popen.return_value = mock_process

        # Configure 2 retries
        data = tasks.load_tasks(self.test_tasks_file)
        data.config.retries = 2
        tasks.save_tasks(self.test_tasks_file, data)

        run_loop(
            self.test_tasks_file,
            verbose=True,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
        )

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].attempts, 2)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.FAILED)

    def test_synchronous_hooks_execution_timing(self):
        """Verifies that a new task starts only AFTER the hooks of the previous task have finished."""
        # 1. Setup two pending tasks.
        now = time.time()
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks = [
                tasks.Task(
                    id="task2",
                    description="Task 2",
                    status=tasks.TaskStatus.PENDING,
                    created_at=now,
                ),
                tasks.Task(
                    id="task1",
                    description="Task 1",
                    status=tasks.TaskStatus.PENDING,
                    created_at=now + 10,
                ),
            ]
            data.config.retries = 1
            data.config.runner = "true"
            tasks.save_tasks(self.test_tasks_file, data)

        task_starts = {}
        hook_ends = {}

        def mocked_run_with_heartbeat(
            cmd, t_file, t_id, verbose, echo_fn, header=None, cwd=None, time_limit=0
        ):
            if header and header.startswith("Hook:"):
                time.sleep(0.1)
                hook_ends[t_id] = time.time()
                return 0, "hook stdout", ""
            else:
                task_starts[t_id] = time.time()
                return 0, "task stdout", ""

        def mocked_finish_task_attempt(t_file, t_id):
            with tasks.lock_tasks(t_file):
                data = tasks.load_tasks(t_file)
                task = next(t for t in data.tasks if t.id == t_id)
                task.requested_status = tasks.TaskStatus.COMPLETED
                task.status = tasks.TaskStatus.COMPLETED
                task.completed_at = time.time()
                tasks.save_tasks(t_file, data)
                return task

        with (
            unittest.mock.patch(
                "lemming.runner.run_with_heartbeat",
                side_effect=mocked_run_with_heartbeat,
            ),
            unittest.mock.patch(
                "lemming.tasks.finish_task_attempt",
                side_effect=mocked_finish_task_attempt,
            ),
            unittest.mock.patch(
                "lemming.prompts.list_hooks", return_value=["test_hook"]
            ),
            unittest.mock.patch(
                "lemming.prompts.prepare_hook_prompt", return_value="Dummy hook"
            ),
        ):
            run_loop(
                self.test_tasks_file,
                verbose=True,
                retry_delay=0,
                yolo=True,
                no_defaults=False,
                runner_args=(),
            )

        self.assertIn("task1", task_starts)
        self.assertIn("task2", task_starts)
        self.assertIn("task2", hook_ends)

        self.assertGreaterEqual(
            task_starts["task1"],
            hook_ends["task2"],
            "Task 1 should start after Task 2 hooks finish",
        )

    @unittest.mock.patch("lemming.runner.run_with_heartbeat")
    def test_run_loop_calls_runner_with_header(self, mock_run):
        # Setup runner mock
        mock_run.return_value = (0, "output", "")

        # Mock finish_task_attempt to return a completed task to end loop
        mock_task = self.initial_data.tasks[0]
        mock_task.status = tasks.TaskStatus.COMPLETED
        with unittest.mock.patch(
            "lemming.tasks.finish_task_attempt", return_value=mock_task
        ):
            run_loop(
                self.test_tasks_file,
                verbose=False,
                retry_delay=0,
                yolo=True,
                no_defaults=False,
                runner_args=(),
            )

        # Verify run_with_heartbeat was called with header="Task Runner"
        args, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("header"), "Task Runner")

    @unittest.mock.patch("lemming.runner.run_with_heartbeat")
    @unittest.mock.patch("time.sleep", return_value=None)
    def test_run_loop_cancelled(self, mock_sleep, mock_run):
        # Simulate a task being cancelled (exit code -15)
        mock_run.return_value = (-15, "cancelled", "error")

        # Configure retries to ensure it DOES NOT retry
        data = tasks.load_tasks(self.test_tasks_file)
        data.config.retries = 3
        tasks.save_tasks(self.test_tasks_file, data)

        run_loop(
            self.test_tasks_file,
            verbose=True,
            retry_delay=1,
            yolo=True,
            no_defaults=False,
            runner_args=(),
        )

        # It should only have 1 attempt
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].attempts, 1)

        # Verify that sleep was NOT called with the retry_delay (1)
        # It might be called with other values if I didn't mock enough,
        # but here it should not be called at all after the break.
        for call in mock_sleep.call_args_list:
            self.assertNotEqual(
                call[0][0], 1, "Should not sleep with retry_delay on cancellation"
            )

    @unittest.mock.patch("lemming.runner.run_with_heartbeat")
    @unittest.mock.patch("time.sleep", return_value=None)
    def test_run_loop_timeout_retries(self, mock_sleep, mock_run):
        """Verifies that a timed-out task is retried without running hooks."""
        # First call: timeout. Second call: timeout again. Exhausts retries.
        mock_run.return_value = (RETURNCODE_TIMEOUT, "output", "")

        data = tasks.load_tasks(self.test_tasks_file)
        data.config.retries = 2
        data.config.time_limit = 60
        tasks.save_tasks(self.test_tasks_file, data)

        run_loop(
            self.test_tasks_file,
            verbose=False,
            retry_delay=0,
            yolo=True,
            no_defaults=False,
            runner_args=(),
        )

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].attempts, 2)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.FAILED)

    @unittest.mock.patch("lemming.runner.run_with_heartbeat")
    @unittest.mock.patch("time.sleep", return_value=None)
    def test_run_loop_passes_time_limit(self, mock_sleep, mock_run):
        """Verifies that time_limit from config is passed to the runner."""
        mock_run.return_value = (0, "output", "")

        data = tasks.load_tasks(self.test_tasks_file)
        data.config.time_limit = 30
        tasks.save_tasks(self.test_tasks_file, data)

        mock_task = self.initial_data.tasks[0]
        mock_task.status = tasks.TaskStatus.COMPLETED
        with unittest.mock.patch(
            "lemming.tasks.finish_task_attempt", return_value=mock_task
        ):
            run_loop(
                self.test_tasks_file,
                verbose=False,
                retry_delay=0,
                yolo=True,
                no_defaults=False,
                runner_args=(),
            )

        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("time_limit"), 30)


if __name__ == "__main__":
    unittest.main()
