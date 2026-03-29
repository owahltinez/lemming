import pathlib
import shutil
import tempfile
import unittest

import click.testing

from lemming import main
from lemming import tasks


class TestMain(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]

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
        )
        tasks.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_main_cli_entry_point(self):
        # Verify that main.cli is accessible and works (it's imported from .cli)
        result = self.cli_runner.invoke(main.cli, self.base_args + ["status"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Initial Task", result.output)


if __name__ == "__main__":
    unittest.main()
