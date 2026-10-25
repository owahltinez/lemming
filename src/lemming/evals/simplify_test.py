import pathlib
import shutil
import tempfile
import unittest
import unittest.mock

from lemming import models
from lemming.evals import fixtures, scenarios, simplify, suites
from lemming.tasks import operations, progress


def _scenario(name: str) -> scenarios.Scenario:
    return next(s for s in simplify.SCENARIOS if s.name == name)


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
        self.assertIn("simplify", registry)
        names = [s.name for s in registry["simplify"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 4)

    def test_action_and_restraint_scenarios_stay_balanced(self):
        # A suite weighted towards action or restraint picks its own winner.
        registry = suites.all_suites()
        names = {s.name for s in registry["simplify"]}
        restraint = {"false-reuse-restraint", "no-orchestration"}
        self.assertEqual(len(restraint), len(names - restraint))


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
        progress.add_progress(
            self.tasks_file,
            "task1",
            "Simplify: _add_legacy() in calc/ops.py duplicates add() "
            "and is never called; left in place, flagging for follow-up.",
        )

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_status_noise_progress_does_not_count(self):
        # Logging "checks passed" over surviving dead code must not pass.
        self.build("fix-or-report-dead-code")
        progress.add_progress(
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
        progress.add_progress(
            self.tasks_file,
            "task1",
            "Simplify: add_for_receipt() and subtract_for_receipt() "
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
        # Moving the visible suite hides the change; the hidden copy does not.
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


class TestNoOrchestrationScenario(ScenarioTestCase):
    def test_recording_progress_passes(self):
        self.build("no-orchestration")
        progress.add_progress(
            self.tasks_file,
            "task1",
            "Simplify: confirmed the cross-file duplication; recorded "
            "for the roadmap hook to decide on a refactor task.",
        )

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_adding_a_task_fails(self):
        self.build("no-orchestration")
        operations.add_task(
            self.tasks_file,
            "Refactor formatting logic shared by ops.py and legacy.py.",
        )

        checks = self.scenario.grade(self.workspace)
        self.assertIn("no-new-tasks", self.failed_names(checks))


if __name__ == "__main__":
    unittest.main()
