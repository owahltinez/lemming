import pathlib
import shutil
import tempfile
import unittest

import click.testing

from lemming import cli, tasks


class TestCLIGoal(unittest.TestCase):
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
            tasks=[],
        )
        tasks.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_goal_no_args(self):
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["goal"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Initial goal", result.output)

    def test_set_goal(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["goal", "Updated goal via CLI"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Long-term goal updated.", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.goal, "Updated goal via CLI")


if __name__ == "__main__":
    unittest.main()
