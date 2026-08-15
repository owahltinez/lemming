import json
import os
import pathlib
import shutil
import stat
import tempfile
import unittest
import unittest.mock

import click.testing

from lemming import models
from lemming.evals import fixtures, roadmap, scenarios, trial


def _scenario(name: str) -> scenarios.Scenario:
    return next(s for s in roadmap.SCENARIOS if s.name == name)


class TrialTestCase(unittest.TestCase):
    def setUp(self):
        self.workspace = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

    def run_trial(
        self,
        scenario: scenarios.Scenario,
        runner: str,
        extra: list[str] | None = None,
    ):
        result = click.testing.CliRunner().invoke(
            trial.main,
            [
                "--tasks-file",
                str(fixtures.tasks_file(self.workspace)),
                "--task-id",
                str(scenario.task_id),
                "--hook",
                str(scenario.hook),
                "--outcome",
                str(scenario.outcome),
                "--runner",
                runner,
                "--time-limit",
                "1",
                *(extra or []),
            ],
            catch_exceptions=False,
        )
        return result

    def run_trial_ok(self, scenario: scenarios.Scenario, runner: str):
        result = self.run_trial(scenario, runner)
        self.assertEqual(result.exit_code, 0, result.output)

    def write_runner_script(self, body: str) -> str:
        """Creates a fake agent that ignores its prompt and runs commands."""
        script = self.workspace / ".lemming" / "fake-runner.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/sh\nset -e\n" + body)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return str(script)


class TestTrialWithNoOpRunner(TrialTestCase):
    def test_fast_exit_scenario_passes(self):
        scenario = _scenario("fast-exit-healthy")
        scenario.build(self.workspace)

        self.run_trial_ok(scenario, runner="true")

        checks = scenario.grade(self.workspace)
        self.assertTrue(scenarios.passed(checks), checks)

    def test_finalizes_completed_task(self):
        scenario = _scenario("fast-exit-healthy")
        scenario.build(self.workspace)

        self.run_trial_ok(scenario, runner="true")

        loaded = fixtures.load_roadmap(self.workspace)
        task1 = next(t for t in loaded.tasks if t.id == "task1")
        self.assertEqual(task1.status, models.TaskStatus.COMPLETED)

    def test_repair_scenario_fails_without_intervention(self):
        scenario = _scenario("repair-exhausted-failure")
        scenario.build(self.workspace)

        self.run_trial_ok(scenario, runner="true")

        checks = scenario.grade(self.workspace)
        self.assertFalse(scenarios.passed(checks), checks)
        loaded = fixtures.load_roadmap(self.workspace)
        task1 = next(t for t in loaded.tasks if t.id == "task1")
        self.assertEqual(task1.status, models.TaskStatus.FAILED)

    def test_dead_runner_fails_the_trial(self):
        # An agent that cannot start (auth failure, missing binary) leaves
        # the workspace pristine; the trial must not report success.
        scenario = _scenario("fast-exit-healthy")
        scenario.build(self.workspace)

        result = self.run_trial(scenario, runner="false")

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Hook runner failed", result.output)


class TestTrialResultFile(TrialTestCase):
    def result_path(self) -> pathlib.Path:
        return self.workspace / ".lemming" / "result.json"

    def run_trial_with_result(self, scenario, runner: str):
        return self.run_trial(
            scenario, runner, extra=["--result-file", str(self.result_path())]
        )

    def read_result(self) -> dict:
        return json.loads(self.result_path().read_text())

    def test_records_exit_codes_on_success(self):
        scenario = _scenario("fast-exit-healthy")
        scenario.build(self.workspace)

        self.run_trial_with_result(scenario, runner="true")

        result = self.read_result()
        self.assertEqual(result["exit_codes"], {scenario.hook: 0})
        self.assertFalse(result["launch_failed"])
        self.assertFalse(result["timed_out"])

    def test_distinguishes_a_runner_that_never_started(self):
        # A missing binary is an infrastructure failure, not the agent
        # behaving badly; the report has to be able to tell them apart.
        scenario = _scenario("fast-exit-healthy")
        scenario.build(self.workspace)

        result = self.run_trial_with_result(
            scenario, runner="/nonexistent/agent"
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(self.read_result()["launch_failed"])

    def test_a_misbehaving_runner_is_not_an_infra_failure(self):
        scenario = _scenario("fast-exit-healthy")
        scenario.build(self.workspace)

        self.run_trial_with_result(scenario, runner="false")

        result = self.read_result()
        self.assertFalse(result["launch_failed"])
        self.assertFalse(result["timed_out"])


class TestTrialWithScriptedRunner(TrialTestCase):
    def test_repairing_agent_passes(self):
        scenario = _scenario("repair-exhausted-failure")
        scenario.build(self.workspace)

        # A well-behaved agent: replace the doomed task with a smaller one
        # through the lemming CLI, exactly as the hook prompt instructs.
        runner = self.write_runner_script(
            "lemming --tasks-file tasks.yml add "
            "'Create a calc/cli.py skeleton with a dispatch table.'\n"
            "lemming --tasks-file tasks.yml supersede task1 "
            "--reason 'split into a smaller prerequisite task'\n"
        )
        self.run_trial_ok(scenario, runner=runner)

        checks = scenario.grade(self.workspace)
        self.assertTrue(scenarios.passed(checks), checks)

    def test_code_editing_agent_fails(self):
        scenario = _scenario("follow-up-without-code-changes")
        scenario.build(self.workspace)

        # A misbehaving agent: fixes the reported bug in source directly
        # instead of scheduling a follow-up task.
        runner = self.write_runner_script("echo 'patched' >> calc/ops.py\n")
        self.run_trial_ok(scenario, runner=runner)

        checks = scenario.grade(self.workspace)
        failed = {c.name for c in checks if not c.passed}
        self.assertIn("no-source-changes", failed)


# What the fake agent is asked to do, and the word it proves it read.
_TASK_PROMPT = "Create marker.txt containing the word beacon."

# Where the fake agent records the argv it was launched with, so a grader
# can tell "the prompt reached the agent" from "the agent did the work".
_ARGV_FILE = "agent-argv.txt"


def _task_scenario() -> scenarios.Scenario:
    """Builds a task scenario graded purely from the finished workspace."""

    def build(workspace: pathlib.Path) -> None:
        fixtures.init_repo(workspace, {"calc/ops.py": "def add(a, b):\n"})

    def grade(workspace: pathlib.Path) -> list[scenarios.Check]:
        argv = workspace / _ARGV_FILE
        marker = workspace / "marker.txt"
        return [
            scenarios.Check(
                name="prompt-delivered",
                passed=argv.is_file() and _TASK_PROMPT in argv.read_text(),
            ),
            scenarios.Check(
                name="marker-written",
                passed=marker.is_file() and "beacon" in marker.read_text(),
            ),
        ]

    return scenarios.Scenario(
        name="task-demo",
        summary="Writes the file the prompt asks for.",
        build=build,
        grade=grade,
        mode="task",
        prompt=_TASK_PROMPT,
    )


class TestTaskModeTrial(TrialTestCase):
    def setUp(self):
        super().setUp()

        # The one-shot keeps its ephemeral roadmap and log under
        # LEMMING_HOME; a test must never write into the real one.
        home = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        patcher = unittest.mock.patch.dict(
            os.environ, {"LEMMING_HOME": str(home)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.scenario = _task_scenario()
        self.scenario.build(self.workspace)
        self.result_file = self.workspace / ".lemming" / "result.json"

    def run_task_trial(self, runner: str):
        return click.testing.CliRunner().invoke(
            trial.main,
            [
                "--mode",
                "task",
                "--workspace",
                str(self.workspace),
                "--prompt",
                _TASK_PROMPT,
                "--runner",
                runner,
                "--time-limit",
                "1",
                "--result-file",
                str(self.result_file),
            ],
            catch_exceptions=False,
        )

    def read_result(self) -> dict:
        return json.loads(self.result_file.read_text())

    def test_runs_the_prompt_and_grades_the_workspace(self):
        # A fake agent that records its prompt, does the work, and reports
        # completion through the CLI, exactly as a real one would.
        agent = self.write_runner_script(
            f'printf "%s\\n" "$*" > {_ARGV_FILE}\n'
            "echo beacon > marker.txt\n"
            'lemming --tasks-file "$LEMMING_PARENT_TASKS_FILE" progress '
            "\"$LEMMING_PARENT_TASK_ID\" 'wrote the marker'\n"
            'lemming --tasks-file "$LEMMING_PARENT_TASKS_FILE" complete '
            '"$LEMMING_PARENT_TASK_ID"\n'
        )

        result = self.run_task_trial(agent)

        self.assertEqual(result.exit_code, 0, result.output)
        checks = self.scenario.grade(self.workspace)
        self.assertTrue(scenarios.passed(checks), checks)
        self.assertEqual(self.read_result()["exit_codes"], {"task": 0})

    def test_distinguishes_an_agent_that_never_started(self):
        # A one-shot reports only whether the task finished, which cannot
        # tell a missing binary from an agent that declined to finish.
        result = self.run_task_trial("/nonexistent/agent")

        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(self.read_result()["launch_failed"])

    def test_an_agent_that_does_nothing_is_not_an_infra_failure(self):
        result = self.run_task_trial("true")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(self.read_result()["launch_failed"])
        # The agent ran and did nothing; that is the grader's verdict to
        # give, not an infrastructure error.
        checks = self.scenario.grade(self.workspace)
        self.assertFalse(scenarios.passed(checks), checks)

    def test_missing_hook_options_are_reported(self):
        # The hook options stopped being click-level requirements so task
        # scenarios need not fake them; a hook trial must still say so.
        result = click.testing.CliRunner().invoke(
            trial.main, ["--runner", "true"]
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--tasks-file", result.output)


if __name__ == "__main__":
    unittest.main()
