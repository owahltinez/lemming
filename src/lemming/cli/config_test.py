import pathlib
import shutil
import tempfile
import unittest

import click.testing

from lemming import cli, tasks


class TestCLIConfig(unittest.TestCase):
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

    def test_config_list(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["config", "list"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Runner:", result.output)
        # Active hooks are discovered from the filesystem and displayed
        self.assertIn("Hooks:", result.output)
        self.assertIn("roadmap", result.output)

    def test_config_set(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["config", "set", "runner", "new-runner"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Updated runner to new-runner", result.output)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.config.runner, "new-runner")


if __name__ == "__main__":
    unittest.main()


class TestCLIConfigModel(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]
        tasks.save_tasks(self.test_tasks_file, tasks.Roadmap(goal="g"))

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_config_set_model_persists(self):
        """The model is a first-class field, not part of the runner string."""
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["config", "set", "model", "fast-model"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.config.model, "fast-model")

    def test_switching_runner_preserves_model(self):
        """A quota hook switching runners must not discard the model."""
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["config", "set", "model", "fast-model"]
        )

        self.cli_runner.invoke(
            cli.cli, self.base_args + ["config", "set", "runner", "codex"]
        )

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.config.runner, "codex")
        self.assertEqual(data.config.model, "fast-model")

    def test_config_set_model_default_clears_pin(self):
        """ "default" hands model choice back to the runner."""
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["config", "set", "model", "fast-model"]
        )

        self.cli_runner.invoke(
            cli.cli, self.base_args + ["config", "set", "model", "default"]
        )

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIsNone(data.config.model)

    def test_config_list_shows_model(self):
        """The model is discoverable without reading the source."""
        self.cli_runner.invoke(
            cli.cli, self.base_args + ["config", "set", "model", "fast-model"]
        )

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["config", "list"]
        )

        self.assertIn("fast-model", result.output)
