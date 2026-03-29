import pathlib
import shutil
import tempfile
import unittest
import click.testing
from lemming import cli
from lemming import tasks


class TestCLIOperations(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--verbose", "--tasks-file", str(self.test_tasks_file)]

        # Scaffold a valid file
        data = tasks.Roadmap(
            context="Initial context",
            tasks=[],
        )
        tasks.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_run_help(self):
        result = self.cli_runner.invoke(cli.cli, ["run", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Starts the orchestrator loop", result.output)

    def test_serve_help(self):
        result = self.cli_runner.invoke(cli.cli, ["serve", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Launches the local web dashboard", result.output)


if __name__ == "__main__":
    unittest.main()
