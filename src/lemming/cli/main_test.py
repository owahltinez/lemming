import os
import pathlib
import shutil
import tempfile
import unittest

import click.testing

from lemming import paths, tasks

# Imported from the package so that every command is registered.
from lemming.cli import cli


class TestCLIMain(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()

    def test_cli_help(self):
        result = self.cli_runner.invoke(cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn(
            "Lemming: An autonomous, iterative task runner", result.output
        )

    def test_command_help_hides_internal_docstrings(self):
        for command_name in cli.commands:
            with self.subTest(command=command_name):
                result = self.cli_runner.invoke(cli, [command_name, "--help"])
                self.assertEqual(result.exit_code, 0)
                self.assertNotIn("Args:", result.output)
                self.assertNotIn("ctx: The click context", result.output)

    def test_corrupted_tasks_file_reports_actionable_error(self):
        with tempfile.TemporaryDirectory() as test_dir:
            tasks_file = pathlib.Path(test_dir) / "tasks.yml"
            tasks_file.write_text("tasks: [unclosed\n", encoding="utf-8")

            result = self.cli_runner.invoke(
                cli, ["--tasks-file", str(tasks_file), "status"]
            )

            self.assertEqual(result.exit_code, 1)
            self.assertIn(str(tasks_file), result.output)
            self.assertIn("tasks.yml.corrupt", result.output)
            self.assertNotIn("Traceback", result.output)
            # The unreadable file is reported, never rewritten.
            self.assertEqual(
                tasks_file.read_text(encoding="utf-8"), "tasks: [unclosed\n"
            )


class TestProjectDirOption(unittest.TestCase):
    """`-C` addresses another project's roadmap from the current directory."""

    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.origin = pathlib.Path.cwd()
        self.here = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.other = pathlib.Path(tempfile.mkdtemp()).resolve()
        os.chdir(self.here)

    def tearDown(self):
        os.chdir(self.origin)
        shutil.rmtree(self.here, ignore_errors=True)
        shutil.rmtree(self.other, ignore_errors=True)

    def test_targets_local_tasks_file_of_other_project(self):
        other_tasks = self.other / "tasks.yml"
        tasks.save_tasks(other_tasks, tasks.Roadmap())

        result = self.cli_runner.invoke(
            cli, ["-C", str(self.other), "add", "Filed from elsewhere"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        descriptions = [
            t.description for t in tasks.load_tasks(other_tasks).tasks
        ]
        self.assertEqual(descriptions, ["Filed from elsewhere"])

    def test_targets_isolated_tasks_file_when_project_has_none(self):
        # Neither project has a local tasks.yml, so the task must land in the
        # target's isolated file rather than the current directory's.
        result = self.cli_runner.invoke(
            cli, ["--project-dir", str(self.other), "add", "Isolated file"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        target = paths.get_tasks_file_for_dir(self.other)
        self.assertNotEqual(target, paths.get_tasks_file_for_dir(self.here))
        descriptions = [t.description for t in tasks.load_tasks(target).tasks]
        self.assertEqual(descriptions, ["Isolated file"])

    def test_working_dir_follows_project_dir(self):
        # Downstream path resolution (.env, local hooks, the runner's cwd)
        # keys off the working directory, not just the tasks file.
        seen: list[pathlib.Path] = []

        @cli.command("probe-working-dir", hidden=True)
        def probe():
            seen.append(paths.get_working_dir(paths.get_default_tasks_file()))

        try:
            result = self.cli_runner.invoke(
                cli, ["-C", str(self.other), "probe-working-dir"]
            )
        finally:
            del cli.commands["probe-working-dir"]

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen, [self.other])

    def test_restores_the_original_working_directory(self):
        self.cli_runner.invoke(cli, ["-C", str(self.other), "status"])

        self.assertEqual(pathlib.Path.cwd(), self.here)

    def test_tasks_file_wins_and_resolves_against_project_dir(self):
        explicit = self.other / "explicit.yml"
        tasks.save_tasks(explicit, tasks.Roadmap())

        result = self.cli_runner.invoke(
            cli,
            [
                "-C",
                str(self.other),
                "--tasks-file",
                "explicit.yml",
                "add",
                "Explicit target",
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        descriptions = [t.description for t in tasks.load_tasks(explicit).tasks]
        self.assertEqual(descriptions, ["Explicit target"])

    def test_rejects_a_missing_project_dir(self):
        result = self.cli_runner.invoke(
            cli, ["-C", str(self.other / "nope"), "status"]
        )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("does not exist", result.output)


if __name__ == "__main__":
    unittest.main()
