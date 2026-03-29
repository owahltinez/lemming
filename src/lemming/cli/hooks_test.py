import pathlib
import shutil
import tempfile
import unittest
import click.testing
from lemming import cli
from lemming import tasks


class TestCLIHooks(unittest.TestCase):
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

    def test_hooks_list(self):
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["hooks", "list"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Available orchestrator hooks:", result.output)

    def test_hooks_reset(self):
        result = self.cli_runner.invoke(cli.cli, self.base_args + ["hooks", "reset"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Hooks reset to default", result.output)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIsNone(data.config.hooks)


if __name__ == "__main__":
    unittest.main()
