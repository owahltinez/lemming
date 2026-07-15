import pathlib
import shutil
import tempfile
import unittest
import unittest.mock

import click.testing

from lemming import cli, hooks, tasks


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

        # Isolate hook discovery from the developer's real global hooks
        self.env_patch = unittest.mock.patch.dict(
            "os.environ", {"LEMMING_HOME": self.test_dir}
        )
        self.env_patch.start()

        # Scaffold a valid file
        data = tasks.Roadmap(
            goal="Initial goal",
            tasks=[],
        )
        tasks.save_tasks(self.test_tasks_file, data)

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

    def test_hooks_list_shows_disabled(self):
        self.local_hooks_dir.mkdir(parents=True)
        (self.local_hooks_dir / "roadmap.md").write_text("", encoding="utf-8")

        result = self._invoke("list")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("disabled", result.output)

    def test_hooks_disable_creates_mask(self):
        result = self._invoke("disable", "roadmap")
        self.assertEqual(result.exit_code, 0)

        # The mask filename keeps the hook's priority (roadmap is 90)
        mask = self.local_hooks_dir / "90-roadmap.md"
        self.assertTrue(mask.exists())
        self.assertEqual(mask.read_text(encoding="utf-8"), "")
        self.assertNotIn(
            "roadmap", hooks.list_hooks(self.test_tasks_file)
        )

    def test_hooks_disable_unknown_hook(self):
        result = self._invoke("disable", "does-not-exist")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("not found", result.output)

    def test_hooks_disable_is_atomic(self):
        """A bad name anywhere in the list disables nothing."""
        result = self._invoke("disable", "readability", "does-not-exist")
        self.assertEqual(result.exit_code, 1)
        self.assertIn(
            "readability", hooks.list_hooks(self.test_tasks_file)
        )

    def test_hooks_disable_refuses_project_override(self):
        self.local_hooks_dir.mkdir(parents=True)
        override = self.local_hooks_dir / "roadmap.md"
        override.write_text("custom prompt", encoding="utf-8")

        result = self._invoke("disable", "roadmap")
        self.assertEqual(result.exit_code, 1)
        # The override must not be clobbered
        self.assertEqual(
            override.read_text(encoding="utf-8"), "custom prompt"
        )

    def test_hooks_enable_removes_mask(self):
        self.local_hooks_dir.mkdir(parents=True)
        mask = self.local_hooks_dir / "roadmap.md"
        mask.write_text("", encoding="utf-8")

        result = self._invoke("enable", "roadmap")
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(mask.exists())
        self.assertIn(
            "roadmap", hooks.list_hooks(self.test_tasks_file)
        )

    def test_hooks_enable_already_enabled(self):
        result = self._invoke("enable", "roadmap")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("already enabled", result.output)

    def test_hooks_enable_refuses_project_override(self):
        self.local_hooks_dir.mkdir(parents=True)
        override = self.local_hooks_dir / "roadmap.md"
        override.write_text("custom prompt", encoding="utf-8")

        result = self._invoke("enable", "roadmap")
        self.assertEqual(result.exit_code, 1)
        self.assertTrue(override.exists())


if __name__ == "__main__":
    unittest.main()
