import pathlib
import shutil
import stat
import tempfile
import unittest

import click.testing

from lemming import models
from lemming.evals import fixtures, roadmap, scenarios, trial


def _scenario(name: str) -> scenarios.Scenario:
    return next(s for s in roadmap.SCENARIOS if s.name == name)


class TrialTestCase(unittest.TestCase):
    def setUp(self):
        self.workspace = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

    def run_trial(self, scenario: scenarios.Scenario, runner: str):
        result = click.testing.CliRunner().invoke(
            trial.main,
            [
                "--tasks-file",
                str(fixtures.tasks_file(self.workspace)),
                "--task-id",
                scenario.task_id,
                "--hook",
                scenario.hook,
                "--outcome",
                str(scenario.outcome),
                "--runner",
                runner,
                "--time-limit",
                "1",
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


class TestTrialWithScriptedRunner(TrialTestCase):
    def test_repairing_agent_passes(self):
        scenario = _scenario("repair-exhausted-failure")
        scenario.build(self.workspace)

        # A well-behaved agent: replace the doomed task with a smaller one
        # through the lemming CLI, exactly as the hook prompt instructs.
        runner = self.write_runner_script(
            "lemming --tasks-file tasks.yml delete task1\n"
            "lemming --tasks-file tasks.yml add "
            "'Create a calc/cli.py skeleton with a dispatch table.'\n"
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


if __name__ == "__main__":
    unittest.main()
