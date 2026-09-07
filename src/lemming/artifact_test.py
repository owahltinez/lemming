"""Tests for out-of-band diagnostic artifacts stored per task."""

import pathlib
import shutil
import tempfile
import unittest

import click.testing

from lemming import models, paths, persistence, prompts
from lemming.cli import main as cli
from lemming.tasks import lifecycle, limits, operations

TRACE = "Traceback (most recent call last):\nAssertionError: boom"


class TestTaskArtifacts(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.tasks_file = pathlib.Path(self.test_dir) / "tasks.yml"
        self.base_args = ["--tasks-file", str(self.tasks_file)]
        self.data = models.Roadmap(
            goal="Ship it",
            tasks=[models.Task(id="task1", description="Fix the thing")],
        )
        persistence.save_tasks(self.tasks_file, self.data)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _invoke(self, *args: str) -> click.testing.Result:
        return self.cli_runner.invoke(cli.cli, self.base_args + list(args))

    def test_write_list_and_read_back(self):
        write = self._invoke("artifact", "task1", "pytest.log", TRACE)
        self.assertEqual(write.exit_code, 0, write.output)
        self.assertIn("task1-artifacts/pytest.log", write.output)

        listing = self._invoke("artifact", "task1")
        self.assertIn("pytest.log", listing.output)

        read = self._invoke("artifact", "task1", "pytest.log")
        self.assertIn("AssertionError: boom", read.output)

    def test_list_says_so_when_empty(self):
        listing = self._invoke("artifact", "task1")

        self.assertEqual(listing.exit_code, 0, listing.output)
        self.assertIn("No artifacts for task task1.", listing.output)

    def test_note_records_a_progress_pointer(self):
        result = self._invoke(
            "artifact", "task1", "pytest.log", TRACE, "--note", "suite fails"
        )
        self.assertEqual(result.exit_code, 0, result.output)

        entry = persistence.load_tasks(self.tasks_file).tasks[0].progress[0]
        self.assertTrue(entry.startswith("suite fails (see "))
        self.assertIn("task1-artifacts/pytest.log", entry)

    def test_oversized_note_writes_nothing(self):
        result = self._invoke(
            "artifact",
            "task1",
            "pytest.log",
            TRACE,
            "--note",
            "x" * limits.MAX_PROGRESS_ENTRY_CHARS,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Shorten --note", result.output)
        self.assertEqual(paths.list_artifacts(self.tasks_file, "task1"), [])
        self.assertEqual(
            persistence.load_tasks(self.tasks_file).tasks[0].progress, []
        )

    def test_rejects_content_without_a_name(self):
        source = pathlib.Path(self.test_dir) / "pytest.log"
        source.write_text(TRACE)

        result = self._invoke("artifact", "task1", "--file", str(source))

        self.assertEqual(result.exit_code, 1)
        self.assertIn("artifact name is required", result.output)
        self.assertEqual(paths.list_artifacts(self.tasks_file, "task1"), [])

    def test_round_trips_content_that_is_not_utf8(self):
        source = pathlib.Path(self.test_dir) / "raw.log"
        source.write_bytes(b"boom \xff\xfe done")

        write = self._invoke(
            "artifact", "task1", "raw.log", "--file", str(source)
        )
        self.assertEqual(write.exit_code, 0, write.output)

        read = self._invoke("artifact", "task1", "raw.log")
        self.assertEqual(read.exit_code, 0, read.output)
        self.assertIn("boom", read.output)
        self.assertIn("done", read.output)

    def test_rejects_names_that_are_paths(self):
        for name in ("../escape.log", "nested/report.txt", "."):
            with self.subTest(name=name):
                result = self._invoke("artifact", "task1", name, TRACE)

                self.assertEqual(result.exit_code, 1)
                self.assertIn("plain filenames", result.output)

    def test_prompt_lists_artifact_names_without_contents(self):
        self._invoke("artifact", "task1", "pytest.log", TRACE)

        prompt = prompts.prepare_prompt(
            self.data, self.data.tasks[0], self.tasks_file
        )

        self.assertIn("## Task Artifacts", prompt)
        self.assertIn("pytest.log", prompt)
        self.assertNotIn("AssertionError: boom", prompt)

    def test_prompt_omits_artifacts_section_when_none(self):
        prompt = prompts.prepare_prompt(
            self.data, self.data.tasks[0], self.tasks_file
        )

        self.assertNotIn("Task Artifacts", prompt)

    def test_reset_keeps_artifacts_but_delete_removes_them(self):
        self._invoke("artifact", "task1", "pytest.log", TRACE)
        artifacts = paths.get_artifacts_dir(self.tasks_file, "task1")

        lifecycle.reset_task_logs(self.tasks_file, "task1")
        self.assertTrue((artifacts / "pytest.log").exists())

        operations.delete_tasks(self.tasks_file, task_id="task1", force=True)
        self.assertFalse((artifacts / "pytest.log").exists())


if __name__ == "__main__":
    unittest.main()
