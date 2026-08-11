import pathlib
import tempfile
import unittest

import click.testing

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


if __name__ == "__main__":
    unittest.main()
