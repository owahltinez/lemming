import pathlib
import shutil
import tempfile
import unittest

from lemming import models, tasks
from lemming.evals import fixtures, readability, scenarios, suites


def _scenario(name: str) -> scenarios.Scenario:
    return next(s for s in readability.SCENARIOS if s.name == name)


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
        self.assertEqual(len(names), 4)


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


class TestScopeLimitScenario(ScenarioTestCase):
    def test_leaving_legacy_alone_passes(self):
        self.build("scope-limited-to-changed-files")

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.failed_names(checks), set())

    def test_cleaning_out_of_scope_file_fails(self):
        self.build("scope-limited-to-changed-files")
        legacy = self.workspace / "calc" / "legacy.py"
        legacy.write_text('"""Legacy."""\n\n\ndef format_report(v):\n')

        checks = self.scenario.grade(self.workspace)
        self.assertIn("out-of-scope-untouched", self.failed_names(checks))


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
