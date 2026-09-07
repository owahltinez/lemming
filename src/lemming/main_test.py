import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

import click.testing

from lemming import main, models, persistence


class TestMain(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]

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

    def test_main_cli_entry_point(self):
        # Verify that main.cli is accessible and works (it's imported from .cli)
        result = self.cli_runner.invoke(main.cli, self.base_args + ["status"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Initial Task", result.output)

    def test_main_refreshes_the_installed_skill(self):
        # The console script is the only caller, so it is the only place a
        # skill under the user's home may be rewritten.
        with (
            mock.patch.object(main, "refresh_skill") as refresh,
            mock.patch.object(main, "cli") as cli,
        ):
            main.main()

        refresh.assert_called_once_with(name="lemming", package="lemming")
        cli.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
