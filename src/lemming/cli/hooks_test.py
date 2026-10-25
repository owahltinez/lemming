import pathlib
import shutil
import tempfile
import unittest
import unittest.mock

import click.testing

from lemming import models, persistence
from lemming.cli import main as cli


class TestCLIHooks(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.test_tasks_file = pathlib.Path(self.test_dir) / "tasks_test.yml"
        self.local_hooks_dir = (
            pathlib.Path(self.test_dir) / ".lemming" / "hooks"
        )
        self.base_args = [
            "--verbose",
            "--tasks-file",
            str(self.test_tasks_file),
        ]

        # Isolate hook discovery from the developer's real global hooks.
        # Must be a directory that does NOT contain the tasks file, or
        # get_working_dir falls back to the CWD instead of the test dir.
        self.env_patch = unittest.mock.patch.dict(
            "os.environ",
            {"LEMMING_HOME": str(pathlib.Path(self.test_dir) / "home")},
        )
        self.env_patch.start()

        # Scaffold a valid file
        data = models.Roadmap(
            goal="Initial goal",
            tasks=[],
        )
        persistence.save_tasks(self.test_tasks_file, data)

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.test_dir)

    def _invoke(self, *args):
        return self.cli_runner.invoke(
            cli.cli, self.base_args + ["hooks", *args]
        )

    def test_hooks_list(self):
        result = self._invoke("list")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("roadmap", result.output)
        self.assertIn("built-in", result.output)
        self.assertIn("runs on failure", result.output)

    def _config_hooks(self):
        return persistence.load_tasks(self.test_tasks_file).config.hooks

    def test_hooks_list_shows_project_defaults(self):
        result = self._invoke("list")

        lines = {
            line.split()[1]: line for line in result.output.splitlines()[1:]
        }
        self.assertIn("enabled", lines["roadmap"])
        self.assertNotIn("enabled", lines["readability"])

    def test_hooks_list_shows_masked(self):
        self.local_hooks_dir.mkdir(parents=True)
        (self.local_hooks_dir / "roadmap.md").write_text("", encoding="utf-8")

        result = self._invoke("list")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("masked", result.output)

    def test_hooks_enable_and_disable_edit_project_default(self):
        result = self._invoke("enable", "ux")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(sorted(self._config_hooks()), ["roadmap", "ux"])

        result = self._invoke("disable", "roadmap", "ux")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self._config_hooks(), [])
        self.assertFalse(self.local_hooks_dir.exists())

    def test_hooks_enable_already_enabled(self):
        result = self._invoke("enable", "roadmap")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("already enabled", result.output)

    def test_hooks_disable_unknown_hook(self):
        result = self._invoke("disable", "does-not-exist")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("not found", result.output)
        self.assertEqual(self._config_hooks(), ["roadmap"])


if __name__ == "__main__":
    unittest.main()
