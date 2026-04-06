import pathlib
import shutil
import tempfile
import unittest
import unittest.mock

from lemming import tasks
from lemming.hooks import run_hooks


class TestHooks(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"

        # Scaffold a valid file
        data = tasks.Roadmap(
            context="Initial context",
            tasks=[
                tasks.Task(
                    id="12345678",
                    description="Initial Task",
                    status=tasks.TaskStatus.PENDING,
                    attempts=0,
                    outcomes=[],
                )
            ],
            config=tasks.RoadmapConfig(retries=3, runner="gemini", hooks=["roadmap"]),
        )
        tasks.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @unittest.mock.patch("lemming.runner.run_with_heartbeat")
    @unittest.mock.patch("lemming.prompts.list_hooks")
    @unittest.mock.patch("lemming.prompts.prepare_hook_prompt")
    def test_run_hooks_success(self, mock_prepare, mock_list, mock_run):
        mock_list.return_value = ["roadmap"]
        mock_prepare.return_value = "Hook Prompt"
        mock_run.return_value = (0, "stdout", "")

        # Task must be IN_PROGRESS for finalization to apply
        tasks.update_task(
            self.test_tasks_file, "12345678", status=tasks.TaskStatus.IN_PROGRESS
        )

        run_hooks(
            self.test_tasks_file,
            "12345678",
            "gemini",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=True,
            final_status=tasks.TaskStatus.COMPLETED,
        )

        self.assertTrue(mock_run.called)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.COMPLETED)

    @unittest.mock.patch("lemming.runner.run_with_heartbeat")
    def test_run_hooks_no_hooks(self, mock_run):
        run_hooks(
            self.test_tasks_file,
            "12345678",
            "gemini",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=True,
            hooks=[],
            final_status=tasks.TaskStatus.COMPLETED,
        )

        self.assertFalse(mock_run.called)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.COMPLETED)

    @unittest.mock.patch("lemming.hooks.prompts.prepare_hook_prompt")
    @unittest.mock.patch("lemming.runner.run_with_heartbeat")
    def test_run_hooks_failure_filters_hooks(self, mock_run, mock_prepare):
        mock_run.return_value = (0, "stdout", "")
        mock_prepare.return_value = "Mock Prompt"

        # Task must be IN_PROGRESS for finalization to apply
        tasks.update_task(
            self.test_tasks_file, "12345678", status=tasks.TaskStatus.IN_PROGRESS
        )

        run_hooks(
            self.test_tasks_file,
            "12345678",
            "gemini",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=True,
            hooks=["readability", "roadmap", "testing"],
            final_status=tasks.TaskStatus.FAILED,
        )

        # It should only run 'roadmap' hook
        self.assertEqual(mock_prepare.call_count, 1)
        self.assertEqual(mock_prepare.call_args[0][0], "roadmap")

    @unittest.mock.patch("lemming.hooks.prompts.prepare_hook_prompt")
    @unittest.mock.patch("lemming.runner.run_with_heartbeat")
    def test_run_hooks_skips_finalization_when_healed(self, mock_run, mock_prepare):
        """If a hook resets the task (heals it), finalization should be skipped."""
        mock_prepare.return_value = "Mock Prompt"

        # Task must be IN_PROGRESS for hooks to run
        tasks.update_task(
            self.test_tasks_file, "12345678", status=tasks.TaskStatus.IN_PROGRESS
        )

        # Simulate the hook resetting the task during execution
        def hook_resets_task(*args, **kwargs):
            tasks.reset_task(self.test_tasks_file, "12345678")
            return (0, "stdout", "")

        mock_run.side_effect = hook_resets_task

        run_hooks(
            self.test_tasks_file,
            "12345678",
            "gemini",
            yolo=True,
            runner_args=(),
            no_defaults=False,
            verbose=True,
            hooks=["roadmap"],
            final_status=tasks.TaskStatus.FAILED,
        )

        # Task should remain PENDING (healed), not FAILED
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.tasks[0].status, tasks.TaskStatus.PENDING)
        self.assertEqual(data.tasks[0].attempts, 0)

    @unittest.mock.patch("lemming.hooks.prompts.prepare_hook_prompt")
    @unittest.mock.patch("lemming.runner.run_with_heartbeat")
    def test_run_hooks_reloads_tasks(self, mock_run, mock_prepare):
        # Create a real Roadmap object to return
        real_data = tasks.load_tasks(self.test_tasks_file)
        mock_run.return_value = (0, "stdout", "")
        mock_prepare.return_value = "Mock Prompt"

        with unittest.mock.patch(
            "lemming.hooks.tasks.load_tasks", return_value=real_data
        ) as mock_load:
            run_hooks(
                self.test_tasks_file,
                "12345678",
                "gemini",
                yolo=True,
                runner_args=(),
                no_defaults=False,
                verbose=True,
                hooks=["h1", "h2"],
            )

            # 1 initial load + 2 hook loads = 3
            self.assertEqual(mock_load.call_count, 3)


if __name__ == "__main__":
    unittest.main()
