import pathlib
import shutil
import tempfile
import unittest

from lemming import models, tasks
from lemming.evals import fixtures, roadmap, scenarios, suites


def _scenario(name: str) -> scenarios.Scenario:
    return next(s for s in roadmap.SCENARIOS if s.name == name)


def _finalize(workspace: pathlib.Path, task_id: str = "task1"):
    """Simulates the trial applying the final status after a no-op hook."""
    tasks.update_task(
        fixtures.tasks_file(workspace),
        task_id,
        status=models.TaskStatus.COMPLETED,
        force=True,
    )


class ScenarioTestCase(unittest.TestCase):
    def setUp(self):
        self.workspace = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

    def check_names(self, checks):
        return {c.name for c in checks if not c.passed}


class TestSuiteRegistry(ScenarioTestCase):
    def test_all_suites_includes_roadmap(self):
        registry = suites.all_suites()
        self.assertIn("roadmap", registry)
        self.assertEqual(len(registry["roadmap"]), 5)

    def test_scenario_names_are_unique(self):
        names = [s.name for s in roadmap.SCENARIOS]
        self.assertEqual(len(names), len(set(names)))


class TestRepairScenario(ScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.scenario = _scenario("repair-exhausted-failure")
        self.scenario.build(self.workspace)
        self.tasks_file = fixtures.tasks_file(self.workspace)

    def test_no_intervention_fails(self):
        checks = self.scenario.grade(self.workspace)
        self.assertIn("repaired", self.check_names(checks))

    def test_naive_reset_fails(self):
        tasks.reset_task(self.tasks_file, "task1")

        checks = self.scenario.grade(self.workspace)
        self.assertIn("repaired", self.check_names(checks))

    def test_rewrite_and_reset_passes(self):
        tasks.update_task(
            self.tasks_file,
            "task1",
            description=(
                "Create calc/ops.py dispatch helper first, then implement "
                "subtract and multiply in calc/cli.py reusing it."
            ),
            force=True,
        )
        tasks.reset_task(self.tasks_file, "task1")

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.check_names(checks), set())

    def test_delete_and_replace_passes(self):
        tasks.delete_tasks(self.tasks_file, "task1")
        tasks.add_task(self.tasks_file, "Add a dispatch helper to calc/ops.py.")

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.check_names(checks), set())

    def test_source_edit_fails(self):
        tasks.delete_tasks(self.tasks_file, "task1")
        (self.workspace / "calc" / "ops.py").write_text("# rewritten\n")

        checks = self.scenario.grade(self.workspace)
        self.assertIn("no-source-changes", self.check_names(checks))


class TestFastExitScenario(ScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.scenario = _scenario("fast-exit-healthy")
        self.scenario.build(self.workspace)
        self.tasks_file = fixtures.tasks_file(self.workspace)

    def test_fast_exit_passes(self):
        _finalize(self.workspace)

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.check_names(checks), set())

    def test_unfinalized_task_fails(self):
        checks = self.scenario.grade(self.workspace)
        self.assertIn("task-finalized", self.check_names(checks))

    def test_churn_fails(self):
        _finalize(self.workspace)
        tasks.add_task(self.tasks_file, "Review code quality holistically.")

        checks = self.scenario.grade(self.workspace)
        self.assertIn("roadmap-untouched", self.check_names(checks))

    def test_deleting_pending_work_fails(self):
        _finalize(self.workspace)
        tasks.delete_tasks(self.tasks_file, "task3")

        checks = self.scenario.grade(self.workspace)
        self.assertIn("roadmap-untouched", self.check_names(checks))


class TestPruneScenario(ScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.scenario = _scenario("prune-redundant-task")
        self.scenario.build(self.workspace)
        self.tasks_file = fixtures.tasks_file(self.workspace)

    def test_delete_redundant_passes(self):
        _finalize(self.workspace)
        tasks.delete_tasks(self.tasks_file, "task2")

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.check_names(checks), set())

    def test_keeping_redundant_fails(self):
        _finalize(self.workspace)

        checks = self.scenario.grade(self.workspace)
        self.assertIn("redundant-task-pruned", self.check_names(checks))


class TestExtendScenario(ScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.scenario = _scenario("extend-goal-unmet")
        self.scenario.build(self.workspace)
        self.tasks_file = fixtures.tasks_file(self.workspace)

    def test_adding_gap_task_passes(self):
        _finalize(self.workspace)
        tasks.add_task(
            self.tasks_file,
            "Implement the multiply command in calc/ops.py with unit tests.",
        )

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.check_names(checks), set())

    def test_fast_exit_fails(self):
        _finalize(self.workspace)

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(
            self.check_names(checks), {"roadmap-extended", "gap-covered"}
        )

    def test_unrelated_task_flags_gap_check_as_advisory(self):
        _finalize(self.workspace)
        tasks.add_task(self.tasks_file, "Polish the README wording.")

        checks = self.scenario.grade(self.workspace)
        # The keyword proxy fails, but only as an inspect-me signal: the
        # trial itself still passes.
        self.assertEqual(self.check_names(checks), {"gap-covered"})
        self.assertTrue(scenarios.passed(checks))


class TestFollowUpScenario(ScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.scenario = _scenario("follow-up-without-code-changes")
        self.scenario.build(self.workspace)
        self.tasks_file = fixtures.tasks_file(self.workspace)

    def test_scheduling_bug_fix_passes(self):
        _finalize(self.workspace)
        tasks.add_task(
            self.tasks_file,
            "Fix subtract() in calc/ops.py to return a - b, with a test.",
        )

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.check_names(checks), set())

    def test_fixing_code_directly_fails(self):
        _finalize(self.workspace)
        ops = self.workspace / "calc" / "ops.py"
        ops.write_text(ops.read_text().replace("a + b\n\n\ndef m", "a - b"))

        checks = self.scenario.grade(self.workspace)
        self.assertIn("no-source-changes", self.check_names(checks))

    def test_ignoring_report_fails(self):
        _finalize(self.workspace)

        checks = self.scenario.grade(self.workspace)
        self.assertIn("follow-up-added", self.check_names(checks))
        self.assertFalse(scenarios.passed(checks))

    def test_vague_follow_up_passes_with_advisory_flag(self):
        _finalize(self.workspace)
        tasks.add_task(
            self.tasks_file, "Address the bug reported by the last task."
        )

        checks = self.scenario.grade(self.workspace)
        self.assertEqual(self.check_names(checks), {"follow-up-targets-bug"})
        self.assertTrue(scenarios.passed(checks))


if __name__ == "__main__":
    unittest.main()
