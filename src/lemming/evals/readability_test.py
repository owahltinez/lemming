import pathlib
import shutil
import tempfile
import unittest
import unittest.mock

from lemming import models
from lemming.evals import fixtures, readability, scenarios, simplify, suites
from lemming.tasks import operations


def _scenario(name: str) -> scenarios.Scenario:
    return next(s for s in readability.SCENARIOS if s.name == name)


# calc/ops.py once a hook has cleared the lint-debt fixture.
_REPAIRED_LINT_DEBT_OPS = '''"""Arithmetic operations for the calculator CLI."""

import math


def add(a: float, b: float) -> float:
    """Returns the sum of two numbers."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Returns the difference of two numbers."""
    return a - b


def average(values: list[float]) -> float:
    """Returns the mean of the given values."""
    total = math.fsum([float(value) for value in values])
    return round(total / max(len(values), 1), 4)
'''


class ScenarioTestCase(unittest.TestCase):
    def setUp(self):
        self.workspace = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)
        orig_which = shutil.which
        patcher = unittest.mock.patch(
            "lemming.evals.readability.shutil.which",
            side_effect=lambda cmd: (
                None if cmd == "docker" else orig_which(cmd)
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, name: str):
        self.scenario = _scenario(name)
        self.scenario.build(self.workspace)
        self.tasks_file = fixtures.tasks_file(self.workspace)
        # Simulate the trial finalizing the finished task after the hook.
        operations.update_task(
            self.tasks_file,
            "task1",
            status=models.TaskStatus.COMPLETED,
            force=True,
        )

    def failed_names(self, checks):
        return {c.name for c in checks if not c.passed}


class TestSuiteRegistry(ScenarioTestCase):
    def test_registered_with_unique_names(self):
        registry = suites.all_suites()
        self.assertIn("readability", registry)
        names = [s.name for s in registry["readability"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 2)

    def test_action_and_restraint_scenarios_stay_balanced(self):
        # A suite weighted towards action or restraint picks its own winner.
        registry = suites.all_suites()
        names = {s.name for s in registry["readability"]}
        restraint = {"clean-fast-exit"}
        self.assertEqual(len(restraint), len(names - restraint))


class TestFastExitScenario(ScenarioTestCase):
    def test_fast_exit_passes(self):
        self.build("clean-fast-exit")

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_gratuitous_edit_fails(self):
        self.build("clean-fast-exit")
        ops = self.workspace / "calc" / "ops.py"
        ops.write_text(ops.read_text() + "\n# reviewed\n")

        checks = self.scenario.grade(self.workspace)
        self.assertIn("no-source-changes", self.failed_names(checks))

    def test_cleaning_out_of_scope_file_fails(self):
        # Tidying the messy out-of-scope legacy.py is the violation caught.
        self.build("clean-fast-exit")
        legacy = self.workspace / "calc" / "legacy.py"
        legacy.write_text('"""Legacy."""\n\n\ndef format_report(v):\n')

        checks = self.scenario.grade(self.workspace)
        self.assertIn("out-of-scope-untouched", self.failed_names(checks))


class TestLintDebtScenario(ScenarioTestCase):
    def test_leaving_the_lint_debt_fails(self):
        self.build("lint-debt-repaid")

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), {"lint-debt-cleared"})

    def test_repairing_the_lint_debt_passes(self):
        self.build("lint-debt-repaid")
        ops = self.workspace / "calc" / "ops.py"
        ops.write_text(_REPAIRED_LINT_DEBT_OPS)

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_deleting_the_offending_function_fails(self):
        # Dropping code is the cheapest way to reach zero findings.
        self.build("lint-debt-repaid")
        ops = self.workspace / "calc" / "ops.py"
        source = _REPAIRED_LINT_DEBT_OPS
        ops.write_text(source[: source.index("def average")])

        checks = self.scenario.grade(self.workspace)
        self.assertIn("interface-preserved", self.failed_names(checks))


class TestFixtureIntegrity(ScenarioTestCase):
    def test_fixture_tests_pass_out_of_the_box(self):
        # A red fixture would fail tests-pass for the wrong reason.
        for scenario in readability.SCENARIOS + simplify.SCENARIOS:
            with self.subTest(scenario=scenario.name):
                workspace = pathlib.Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)
                scenario.build(workspace)
                check = readability._check_tests_pass(workspace)
                self.assertTrue(check.passed, check.detail)

    def test_check_tests_pass_uses_docker_when_available(self):
        completed = unittest.mock.Mock(returncode=0, stderr="")
        with (
            unittest.mock.patch(
                "lemming.evals.readability.shutil.which",
                return_value="/usr/bin/docker",
            ),
            unittest.mock.patch(
                "lemming.evals.readability.subprocess.run",
                return_value=completed,
            ) as mock_run,
        ):
            check = readability._check_tests_pass(self.workspace)

        self.assertTrue(check.passed)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(
            cmd,
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "-v",
                f"{self.workspace}:/workspace:ro",
                "-w",
                "/workspace",
                "lemming-evals",
                "python3",
                "-m",
                "unittest",
                "discover",
                "-p",
                "*_test.py",
            ],
        )


if __name__ == "__main__":
    unittest.main()
