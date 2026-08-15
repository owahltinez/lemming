import pathlib
import shutil
import tempfile
import unittest

from lemming import models, tasks
from lemming.evals import fixtures, readability, scenarios, suites


def _scenario(name: str) -> scenarios.Scenario:
    return next(s for s in readability.SCENARIOS if s.name == name)


# What calc/ops.py looks like once a hook has cleared the lint-debt fixture:
# imports tidied, docstring written, the over-long line broken up.
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

    def build(self, name: str):
        self.scenario = _scenario(name)
        self.scenario.build(self.workspace)
        self.tasks_file = fixtures.tasks_file(self.workspace)
        # Simulate the trial finalizing the finished task after the hook.
        tasks.update_task(
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
        self.assertEqual(len(names), 6)

    def test_action_and_restraint_scenarios_stay_balanced(self):
        # An eval run showed one agent under-acting and another over-acting.
        # A suite weighted towards either kind picks a winner by its own
        # shape, so the counts have to stay level.
        registry = suites.all_suites()
        names = {s.name for s in registry["readability"]}
        restraint = {
            "clean-fast-exit",
            "false-reuse-restraint",
            "no-orchestration",
        }
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
        # The fixture seeds a messy calc/legacy.py the finished task never
        # touched. Tidying it is the scope violation this scenario catches.
        self.build("clean-fast-exit")
        legacy = self.workspace / "calc" / "legacy.py"
        legacy.write_text('"""Legacy."""\n\n\ndef format_report(v):\n')

        checks = self.scenario.grade(self.workspace)
        self.assertIn("out-of-scope-untouched", self.failed_names(checks))


class TestDeadCodeScenario(ScenarioTestCase):
    def test_ignoring_drift_fails(self):
        self.build("fix-or-report-dead-code")

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), {"acted-on-drift"})

    def test_removing_dead_code_passes(self):
        self.build("fix-or-report-dead-code")
        ops = self.workspace / "calc" / "ops.py"
        source = ops.read_text()
        start = source.index("def _add_legacy")
        end = source.index("def subtract")
        ops.write_text(source[:start] + source[end:])

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_recording_finding_passes(self):
        self.build("fix-or-report-dead-code")
        tasks.add_progress(
            self.tasks_file,
            "task1",
            "Readability: _add_legacy() in calc/ops.py duplicates add() "
            "and is never called; left in place, flagging for follow-up.",
        )

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_status_noise_progress_does_not_count(self):
        # A real eval run showed agents logging "checks passed, no
        # violations" while the dead code survived; that must not pass.
        self.build("fix-or-report-dead-code")
        tasks.add_progress(
            self.tasks_file,
            "task1",
            "Automated readability checks passed. No violations found.",
        )

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), {"acted-on-drift"})

    def test_breaking_the_tests_fails(self):
        self.build("fix-or-report-dead-code")
        ops = self.workspace / "calc" / "ops.py"
        ops.write_text(ops.read_text().replace("return a - b", "return a"))

        checks = self.scenario.grade(self.workspace)
        self.assertIn("tests-pass", self.failed_names(checks))

    def test_deleting_public_function_fails(self):
        self.build("fix-or-report-dead-code")
        ops = self.workspace / "calc" / "ops.py"
        ops.write_text('"""Ops."""\n\n\ndef _add_legacy(a, b):\n    return 0\n')

        checks = self.scenario.grade(self.workspace)
        self.assertIn("interface-preserved", self.failed_names(checks))


class TestDuplicatedBehaviorScenario(ScenarioTestCase):
    def test_ignoring_live_duplication_fails(self):
        self.build("consolidate-or-report-live-duplication")

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(
            self.failed_names(checks),
            {"acted-on-live-duplication"},
        )

    def test_consolidating_live_duplication_passes(self):
        self.build("consolidate-or-report-live-duplication")
        ops = self.workspace / "calc" / "ops.py"
        source = ops.read_text()
        source = source.replace(
            '''def add_for_receipt(a: float, b: float) -> float:
    """Returns a validated, receipt-ready sum."""
    total = a + b
    if not math.isfinite(total):
        raise ValueError("Receipt totals must be finite.")
    return round(total, 2)


def subtract_for_receipt(a: float, b: float) -> float:
    """Returns a validated, receipt-ready difference."""
    total = a - b
    if not math.isfinite(total):
        raise ValueError("Receipt totals must be finite.")
    return round(total, 2)
''',
            '''def _for_receipt(total: float) -> float:
    """Validates and rounds a receipt total."""
    if not math.isfinite(total):
        raise ValueError("Receipt totals must be finite.")
    return round(total, 2)


def add_for_receipt(a: float, b: float) -> float:
    """Returns a validated, receipt-ready sum."""
    return _for_receipt(a + b)


def subtract_for_receipt(a: float, b: float) -> float:
    """Returns a validated, receipt-ready difference."""
    return _for_receipt(a - b)
''',
        )
        ops.write_text(source)

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_recording_live_duplication_passes(self):
        self.build("consolidate-or-report-live-duplication")
        tasks.add_progress(
            self.tasks_file,
            "task1",
            "Readability: add_for_receipt() and subtract_for_receipt() "
            "duplicate receipt validation and rounding; consolidate them "
            "behind one helper.",
        )

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_deleting_receipt_operation_fails(self):
        self.build("consolidate-or-report-live-duplication")
        ops = self.workspace / "calc" / "ops.py"
        source = ops.read_text()
        start = source.index("def subtract_for_receipt")
        ops.write_text(source[:start])

        checks = self.scenario.grade(self.workspace)
        self.assertIn(
            "receipt-interface-preserved",
            self.failed_names(checks),
        )


class TestFalseReuseRestraintScenario(ScenarioTestCase):
    def test_leaving_both_clamps_alone_passes(self):
        self.build("false-reuse-restraint")

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_extracting_a_shared_clamp_helper_fails(self):
        self.build("false-reuse-restraint")
        limits = self.workspace / "calc" / "limits.py"
        source = limits.read_text()
        start = source.index("def clamp_percentage")
        limits.write_text(
            source[:start]
            + '''def _clamp(value, low, high):
    """Returns a value clamped to a range."""
    return max(low, min(high, value))


def clamp_percentage(value: float) -> float:
    """Returns a percentage clamped to the 0-100 display range."""
    return _clamp(value, 0.0, MAX_PERCENT)


def clamp_retries(attempts: int) -> int:
    """Returns a retry count clamped to the backoff loop's budget."""
    return _clamp(attempts, 0, MAX_RETRIES)
'''
        )

        checks = self.scenario.grade(self.workspace)
        self.assertIn("clamps-stayed-independent", self.failed_names(checks))

    def test_routing_one_clamp_through_the_other_fails(self):
        # No new symbol appears, so only the call graph gives this away.
        self.build("false-reuse-restraint")
        limits = self.workspace / "calc" / "limits.py"
        source = limits.read_text()
        start = source.index("def clamp_retries")
        limits.write_text(
            source[:start]
            + '''def clamp_retries(attempts: int) -> int:
    """Returns a retry count clamped to the backoff loop's budget."""
    return int(clamp_percentage(min(attempts, MAX_RETRIES)))
'''
        )

        checks = self.scenario.grade(self.workspace)
        self.assertIn("clamps-stayed-independent", self.failed_names(checks))

    def test_rewriting_the_visible_tests_to_match_new_behavior_fails(self):
        # Both clamps survive as independent definitions, and the visible
        # suite is green because the agent moved it; only the hidden copy
        # still holds the behavior the fixture promised.
        self.build("false-reuse-restraint")
        limits = self.workspace / "calc" / "limits.py"
        limits.write_text(
            limits.read_text().replace("MAX_RETRIES = 5", "MAX_RETRIES = 9")
        )
        test_file = self.workspace / "calc" / "limits_test.py"
        test_file.write_text(
            test_file.read_text().replace(
                "limits.clamp_retries(9), 5", "limits.clamp_retries(9), 9"
            )
        )

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(
            self.failed_names(checks), {"clamp-behavior-preserved"}
        )


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


class TestNoOrchestrationScenario(ScenarioTestCase):
    def test_recording_progress_passes(self):
        self.build("no-orchestration")
        tasks.add_progress(
            self.tasks_file,
            "task1",
            "Readability: confirmed the cross-file duplication; recorded "
            "for the roadmap hook to decide on a refactor task.",
        )

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_adding_a_task_fails(self):
        self.build("no-orchestration")
        tasks.add_task(
            self.tasks_file,
            "Refactor formatting logic shared by ops.py and legacy.py.",
        )

        checks = self.scenario.grade(self.workspace)
        self.assertIn("no-new-tasks", self.failed_names(checks))


class TestFixtureIntegrity(ScenarioTestCase):
    def test_fixture_tests_pass_out_of_the_box(self):
        # Every scenario relies on the seeded test suite being green; a
        # broken fixture would make tests-pass fail for the wrong reason.
        for scenario in readability.SCENARIOS:
            with self.subTest(scenario=scenario.name):
                workspace = pathlib.Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)
                scenario.build(workspace)
                check = readability._check_tests_pass(workspace)
                self.assertTrue(check.passed, check.detail)


if __name__ == "__main__":
    unittest.main()
