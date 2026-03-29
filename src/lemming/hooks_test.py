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


if __name__ == "__main__":
    unittest.main()
