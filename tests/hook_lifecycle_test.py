import os
import pathlib
import shutil
import tempfile
import unittest
import unittest.mock
import click.testing
from lemming import main
from lemming import tasks
from lemming import runner


class TestHookLifecycle(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]

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
            config=tasks.RoadmapConfig(
                hooks=["roadmap"], retries=1, runner="mock-runner"
            ),
        )
        tasks.save_tasks(self.test_tasks_file, self.initial_data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @unittest.mock.patch("subprocess.Popen")
    def test_premature_completion_reproduction(self, mock_popen):
        """
        Reproduce the issue where a task is marked as completed BEFORE hooks finish.
        """
        # 1. Mock the main task runner
        mock_task_process = unittest.mock.MagicMock()
        mock_task_process.pid = 12345
        mock_task_process.returncode = 0
        mock_task_process.stdout = iter(["Task output\n"])

        def task_wait_side_effect():
            # Simulate the agent calling 'lemming complete'
            # We must set LEMMING_PARENT_TASK_ID to simulate being inside the agent
            os.environ["LEMMING_PARENT_TASK_ID"] = "task1"
            try:
                self.cli_runner.invoke(
                    main.cli, self.base_args + ["outcome", "task1", "Finding"]
                )
                self.cli_runner.invoke(main.cli, self.base_args + ["complete", "task1"])
            finally:
                del os.environ["LEMMING_PARENT_TASK_ID"]
            return 0

        mock_task_process.wait.side_effect = task_wait_side_effect
        mock_task_process.poll.return_value = 0

        # 2. Mock the hook runner
        mock_hook_process = unittest.mock.MagicMock()
        mock_hook_process.pid = 67890
        mock_hook_process.returncode = 0
        mock_hook_process.stdout = iter(["Hook output\n"])

        # Capture the status when the hook is running
        self.status_during_hook = None
        self.requested_status_during_hook = None

        def hook_wait_side_effect():
            data = tasks.load_tasks(self.test_tasks_file)
            self.status_during_hook = data.tasks[0].status
            self.requested_status_during_hook = data.tasks[0].requested_status
            return 0

        mock_hook_process.wait.side_effect = hook_wait_side_effect
        mock_hook_process.poll.return_value = 0

        # Popen will be called twice: once for the task, once for the hook
        mock_popen.side_effect = [mock_task_process, mock_hook_process]

        # Run the loop
        self.cli_runner.invoke(main.cli, self.base_args + ["run"])

        # Wait a bit for the background thread if it's still running
        import time

        for _ in range(20):
            if self.status_during_hook is not None:
                break
            time.sleep(0.1)

        # Verify change: status is 'in_progress' during the hook now
        print(f"\nStatus during hook: {self.status_during_hook}")
        self.assertEqual(self.status_during_hook, tasks.TaskStatus.IN_PROGRESS)
        self.assertEqual(self.requested_status_during_hook, tasks.TaskStatus.COMPLETED)

        # Final status should be 'completed' after hooks
        # Wait for the background thread to finalize
        for _ in range(20):
            data = tasks.load_tasks(self.test_tasks_file)
            if data.tasks[0].status == tasks.TaskStatus.COMPLETED:
                break
            time.sleep(0.1)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.COMPLETED)

    def test_symlink_recreation_fixed(self):
        """
        Verify that deleting a symlink DOES NOT result in it being recreated automatically.
        """
        # Setup mock lemming home
        with unittest.mock.patch("lemming.paths.get_global_hooks_dir") as mock_get_dir:
            temp_hooks_dir = pathlib.Path(self.test_dir) / "global_hooks_fixed"
            temp_hooks_dir.mkdir(parents=True, exist_ok=True)
            mock_get_dir.return_value = temp_hooks_dir

            # Run ensure_hooks_symlinked explicitly
            runner.ensure_hooks_symlinked()
            roadmap_symlink = temp_hooks_dir / "roadmap.md"
            self.assertTrue(roadmap_symlink.is_symlink())

            # Delete the symlink
            roadmap_symlink.unlink()
            self.assertFalse(roadmap_symlink.exists())

            # Now run list_hooks() which NO LONGER calls ensure_hooks_symlinked automatically
            runner.list_hooks()

            # Deletion is now respected
            self.assertFalse(roadmap_symlink.exists())
            print("\nSymlink was NOT recreated (the fix works)")

            # But the hook is still in the list because of the built-in fallback
            hooks = runner.list_hooks()
            self.assertIn("roadmap", hooks)
            print("Hook 'roadmap' is still available via built-in fallback")

            # lemming hooks install should restore it
            self.cli_runner.invoke(main.cli, ["hooks", "install"])
            self.assertTrue(roadmap_symlink.is_symlink())
            print("lemming hooks install restored the symlink")

    @unittest.mock.patch("subprocess.Popen")
    def test_final_failure_hooks_keep_in_progress(self, mock_popen):
        """
        Verify that tasks are marked as IN_PROGRESS during final failure hooks (when retries are exhausted).
        """
        # 1. Setup a task that will fail
        # Set attempts to 1, retries to 1. So it should trigger final failure hooks.
        with tasks.lock_tasks(self.test_tasks_file):
            data = tasks.load_tasks(self.test_tasks_file)
            data.tasks[0].attempts = 0
            data.config.retries = 1
            tasks.save_tasks(self.test_tasks_file, data)

        # Mock the runner to fail (returncode 1)
        mock_task_process = unittest.mock.MagicMock()
        mock_task_process.pid = 12345
        mock_task_process.returncode = 1
        mock_task_process.stdout = iter(["Task failed\n"])
        mock_task_process.wait.return_value = 1
        mock_task_process.poll.return_value = 1

        # 2. Mock the hook runner
        mock_hook_process = unittest.mock.MagicMock()
        mock_hook_process.pid = 67890
        mock_hook_process.returncode = 0
        mock_hook_process.stdout = iter(["Hook output\n"])

        self.status_during_final_hook = None
        self.requested_status_during_final_hook = None

        def hook_wait_side_effect():
            data = tasks.load_tasks(self.test_tasks_file)
            self.status_during_final_hook = data.tasks[0].status
            self.requested_status_during_final_hook = data.tasks[0].requested_status
            return 0

        mock_hook_process.wait.side_effect = hook_wait_side_effect
        mock_hook_process.poll.return_value = 0

        # Popen will be called twice: task execution, then final failure hook
        mock_popen.side_effect = [mock_task_process, mock_hook_process]

        # Run the loop
        self.cli_runner.invoke(main.cli, self.base_args + ["run"])

        # Verify status during hook
        self.assertEqual(self.status_during_final_hook, tasks.TaskStatus.IN_PROGRESS)
        self.assertEqual(
            self.requested_status_during_final_hook, tasks.TaskStatus.FAILED
        )

        # Final status should be 'failed' after hooks
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
