"""Tests for the long-form task brief delivered alongside the description."""

import pathlib
import shutil
import tempfile
import unittest

import click.testing

from lemming import cli, models, paths, prompts, tasks

EVIDENCE = "Measured: first paint 2.4s. The failing selector is [data-x=1]."


class TestTaskBrief(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.tasks_file = pathlib.Path(self.test_dir) / "tasks.yml"
        self.base_args = ["--tasks-file", str(self.tasks_file)]
        self.data = models.Roadmap(
            goal="Ship it",
            tasks=[models.Task(id="task1", description="Fix the thing")],
        )
        tasks.save_tasks(self.tasks_file, self.data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_brief_file_path_is_per_task(self):
        path = paths.get_brief_file(self.tasks_file, "task1")

        self.assertTrue(str(path).endswith("task1-brief.md"))

    def test_prompt_includes_brief_when_present(self):
        """The runner must receive the evidence without being told to look."""
        paths.get_brief_file(self.tasks_file, "task1").write_text(EVIDENCE)

        prompt = prompts.prepare_prompt(
            self.data, self.data.tasks[0], self.tasks_file
        )

        self.assertIn(EVIDENCE, prompt)

    def test_prompt_omits_brief_section_when_absent(self):
        """No brief means no empty scaffolding in the prompt."""
        prompt = prompts.prepare_prompt(
            self.data, self.data.tasks[0], self.tasks_file
        )

        self.assertNotIn("Task Brief", prompt)

    def test_brief_command_writes_and_reads_back(self):
        write = self.cli_runner.invoke(
            cli.cli, self.base_args + ["brief", "task1", EVIDENCE]
        )
        self.assertEqual(write.exit_code, 0, write.output)

        read = self.cli_runner.invoke(
            cli.cli, self.base_args + ["brief", "task1"]
        )

        self.assertEqual(read.exit_code, 0, read.output)
        self.assertIn(EVIDENCE, read.output)

    def test_brief_command_reads_from_stdin(self):
        result = self.cli_runner.invoke(
            cli.cli,
            self.base_args + ["brief", "task1", "--file", "-"],
            input=EVIDENCE,
        )

        self.assertEqual(result.exit_code, 0, result.output)
        stored = paths.get_brief_file(self.tasks_file, "task1").read_text()
        self.assertIn(EVIDENCE, stored)

    def test_brief_reports_when_missing(self):
        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["brief", "task1"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No brief", result.output)

    def test_brief_has_no_length_cap(self):
        """The brief is where evidence too large for a description belongs."""
        long_evidence = "y" * (tasks.MAX_TASK_DESCRIPTION_CHARS * 3)

        result = self.cli_runner.invoke(
            cli.cli, self.base_args + ["brief", "task1", long_evidence]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        stored = paths.get_brief_file(self.tasks_file, "task1").read_text()
        self.assertEqual(len(stored.strip()), len(long_evidence))

    def test_description_cap_error_points_at_the_brief(self):
        """Hitting the cap must name the supported way to attach evidence."""
        with self.assertRaises(ValueError) as caught:
            tasks.add_task(self.tasks_file, "z" * 5000)

        self.assertIn("lemming brief", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
