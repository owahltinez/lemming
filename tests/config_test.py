import pathlib
import shutil
import tempfile
import unittest
import yaml
import click.testing

from lemming import main
from lemming import tasks


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.base_args = ["--tasks-file", str(self.test_tasks_file)]

        # Scaffold a valid file with default config
        data = {
            "context": "Initial context",
            "tasks": [],
            "config": {"retries": 3, "runner": "gemini", "hooks": ["roadmap"]},
        }
        with open(self.test_tasks_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_config_list(self):
        result = self.cli_runner.invoke(main.cli, self.base_args + ["config", "list"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Runner:        gemini", result.output)
        self.assertIn("Retries:       3", result.output)
        self.assertIn("Hooks:         roadmap", result.output)

    def test_config_set_runner(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "runner", "aider"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Updated runner to aider", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.config.runner, "aider")

    def test_config_set_retries(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "retries", "5"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Updated retries to 5", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.config.retries, 5)

    def test_config_set_hooks(self):
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "hooks", "roadmap,lint"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Updated hooks to roadmap,lint", result.output)

        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.config.hooks, ["roadmap", "lint"])

    def test_hooks_list(self):
        # Create a local hook
        hooks_dir = pathlib.Path(self.test_dir) / ".lemming" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "myhook.md").write_text("prompt", encoding="utf-8")

        result = self.cli_runner.invoke(main.cli, self.base_args + ["hooks", "list"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("myhook", result.output)
        self.assertIn("roadmap", result.output)
        self.assertIn("[active]", result.output)  # roadmap is active by default

    def test_hooks_enable_disable_reset(self):
        # Create a local hook
        hooks_dir = pathlib.Path(self.test_dir) / ".lemming" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "myhook.md").write_text("prompt", encoding="utf-8")

        # Initially both are active because config.hooks is None (after reset or by default if not set)
        # But in setUp we set it to ["roadmap"]
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.config.hooks, ["roadmap"])

        # Enable myhook
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["hooks", "enable", "myhook"]
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIn("myhook", data.config.hooks)
        self.assertIn("roadmap", data.config.hooks)

        # Disable roadmap
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["hooks", "disable", "roadmap"]
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertNotIn("roadmap", data.config.hooks)
        self.assertEqual(data.config.hooks, ["myhook"])

        # Reset
        result = self.cli_runner.invoke(main.cli, self.base_args + ["hooks", "reset"])
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIsNone(data.config.hooks)

        # Disable while None (should transition to explicit list minus one)
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["hooks", "disable", "myhook"]
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIsNotNone(data.config.hooks)
        self.assertNotIn("myhook", data.config.hooks)
        self.assertIn("roadmap", data.config.hooks)

    def test_config_set_hooks_default(self):
        # Set to specific
        self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "hooks", "roadmap"]
        )
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertEqual(data.config.hooks, ["roadmap"])

        # Reset to default
        result = self.cli_runner.invoke(
            main.cli, self.base_args + ["config", "set", "hooks", "default"]
        )
        self.assertEqual(result.exit_code, 0)
        data = tasks.load_tasks(self.test_tasks_file)
        self.assertIsNone(data.config.hooks)
