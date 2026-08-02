"""Tests for machine-readable status and logs output."""

import json
import pathlib
import shutil
import tempfile
import unittest

import click.testing

from lemming import cli, paths, tasks

LONG_DESCRIPTION = "x" * 1500


class TestMachineOutput(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()
        self.test_dir = tempfile.mkdtemp()
        self.tasks_file = pathlib.Path(self.test_dir) / "tasks.yml"
        self.base_args = ["--tasks-file", str(self.tasks_file)]
        tasks.save_tasks(
            self.tasks_file,
            tasks.Roadmap(
                goal="Ship it",
                tasks=[
                    tasks.Task(
                        id="task1",
                        description=LONG_DESCRIPTION,
                        status=tasks.TaskStatus.IN_PROGRESS,
                        model="fast-model",
                        resolved_command="agy --model fast-model",
                    ),
                    tasks.Task(id="task2", description=LONG_DESCRIPTION),
                ],
                config=tasks.RoadmapConfig(runner="agy", model="fast-model"),
            ),
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _invoke(self, *args):
        result = self.cli_runner.invoke(cli.cli, self.base_args + list(args))
        self.assertEqual(result.exit_code, 0, result.output)
        return result

    def test_status_json_is_parseable(self):
        """A manager script must not have to parse the private state file."""
        payload = json.loads(self._invoke("status", "--json").output)

        self.assertEqual(payload["goal"], "Ship it")
        self.assertEqual(len(payload["tasks"]), 2)
        self.assertIn("loop_running", payload)

    def test_status_json_exposes_provenance_and_config(self):
        """Comparing models is the point, so both must be machine-readable."""
        payload = json.loads(self._invoke("status", "--json").output)

        self.assertEqual(payload["config"]["model"], "fast-model")
        running = payload["tasks"][0]
        self.assertEqual(running["model"], "fast-model")
        self.assertEqual(running["resolved_command"], "agy --model fast-model")

    def test_status_json_for_single_task(self):
        payload = json.loads(self._invoke("status", "task1", "--json").output)

        self.assertEqual(payload["id"], "task1")
        self.assertEqual(payload["status"], "in_progress")

    def test_status_brief_omits_descriptions(self):
        """Answering "what is running?" must not cost 20KB of prose."""
        output = self._invoke("status", "--brief").output

        self.assertNotIn(LONG_DESCRIPTION, output)
        self.assertIn("task1", output)
        self.assertIn("task2", output)

    def test_status_json_brief_drops_description_field(self):
        payload = json.loads(self._invoke("status", "--json", "--brief").output)

        for task in payload["tasks"]:
            self.assertNotIn("description", task)
        self.assertIn("status", payload["tasks"][0])

    def test_status_json_is_the_only_output(self):
        """Stray human text would break json.loads for callers."""
        output = self._invoke("status", "--json").output

        self.assertTrue(output.lstrip().startswith("{"))

    def test_logs_json_wraps_content(self):
        log_file = paths.get_log_file(self.tasks_file, "task1")
        log_file.write_text("line one\nline two\n")

        payload = json.loads(self._invoke("logs", "task1", "--json").output)

        self.assertEqual(payload["task_id"], "task1")
        self.assertIn("line two", payload["content"])


if __name__ == "__main__":
    unittest.main()
