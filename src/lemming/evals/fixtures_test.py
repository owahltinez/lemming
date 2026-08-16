import pathlib
import shutil
import tempfile
import unittest

from lemming import models
from lemming.evals import fixtures


class TestLoadProject(unittest.TestCase):
    def setUp(self):
        self.workspace = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

    def test_reads_nested_files_as_workspace_relative_paths(self):
        files = fixtures.load_project("task/summary")

        self.assertEqual(
            sorted(files),
            [
                "README.md",
                "stats/__init__.py",
                "stats/summary.py",
                "stats/summary_test.py",
            ],
        )
        self.assertIn("def mean(", files["stats/summary.py"])

    def test_loaded_project_seeds_a_workspace_unchanged(self):
        # A project reaches the workspace exactly as it is stored.
        files = fixtures.load_project("task/summary")
        fixtures.init_repo(self.workspace, files)

        source = fixtures.PROJECTS_DIR / "task/summary/stats/summary.py"
        self.assertEqual(
            (self.workspace / "stats/summary.py").read_text(),
            source.read_text(),
        )


class TestInitRepo(unittest.TestCase):
    def setUp(self):
        self.workspace = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

    def test_creates_files_and_baseline_commit(self):
        fixtures.init_repo(self.workspace, {"pkg/mod.py": "X = 1\n"})

        self.assertEqual((self.workspace / "pkg/mod.py").read_text(), "X = 1\n")
        self.assertTrue((self.workspace / ".git").is_dir())
        self.assertEqual(fixtures.dirty_paths(self.workspace), [])

    def test_dirty_paths_reports_source_changes(self):
        fixtures.init_repo(self.workspace, {"pkg/mod.py": "X = 1\n"})

        (self.workspace / "pkg/mod.py").write_text("X = 2\n")
        (self.workspace / "rogue.py").write_text("Y = 3\n")

        self.assertEqual(
            sorted(fixtures.dirty_paths(self.workspace)),
            ["pkg/mod.py", "rogue.py"],
        )

    def test_dirty_paths_ignores_eval_owned_files(self):
        fixtures.init_repo(self.workspace, {"pkg/mod.py": "X = 1\n"})

        # Harness-owned state must not count as source drift.
        fixtures.save_roadmap(self.workspace, models.Roadmap(goal="g"))
        (self.workspace / ".lemming").mkdir()
        (self.workspace / ".lemming" / "state").write_text("x")
        (self.workspace / "runner.log").write_text("log")

        # Nor must the caches left behind by tools the agent ran.
        (self.workspace / ".pytest_cache").mkdir()
        (self.workspace / ".pytest_cache" / "v").write_text("x")

        self.assertEqual(fixtures.dirty_paths(self.workspace), [])
        self.assertEqual(fixtures.changed_since_baseline(self.workspace), [])

    def test_changes_land_in_a_second_commit(self):
        # Hook scenarios review a real diff, not a prose description.
        fixtures.init_repo(
            self.workspace,
            {"pkg/mod.py": "X = 1\n", "pkg/other.py": "Y = 1\n"},
            changes={"pkg/mod.py": "X = 2\n", "pkg/new.py": "Z = 1\n"},
        )

        self.assertEqual((self.workspace / "pkg/mod.py").read_text(), "X = 2\n")
        self.assertEqual(
            sorted(fixtures.changed_paths(self.workspace)),
            ["pkg/mod.py", "pkg/new.py"],
        )
        self.assertEqual(fixtures.dirty_paths(self.workspace), [])

    def test_without_changes_there_is_no_task_commit(self):
        fixtures.init_repo(self.workspace, {"pkg/mod.py": "X = 1\n"})

        self.assertEqual(fixtures.changed_paths(self.workspace), [])
        self.assertEqual(fixtures.dirty_paths(self.workspace), [])

    def test_baseline_diff_survives_the_agent_committing(self):
        # Committing leaves a clean status but not a clean baseline.
        fixtures.init_repo(self.workspace, {"pkg/mod.py": "X = 1\n"})
        (self.workspace / "pkg/mod.py").write_text("X = 1\nY = 2\n")
        (self.workspace / "REPORT.md").write_text("# Report\n")
        fixtures._git(self.workspace, "add", "--all")
        fixtures._git(self.workspace, "commit", "--quiet", "-m", "Agent work")

        self.assertEqual(fixtures.dirty_paths(self.workspace), [])
        self.assertEqual(
            fixtures.changed_since_baseline(self.workspace),
            ["REPORT.md", "pkg/mod.py"],
        )
        self.assertEqual(fixtures.added_lines_since_baseline(self.workspace), 2)

    def test_baseline_diff_sees_uncommitted_and_untracked_work(self):
        fixtures.init_repo(self.workspace, {"pkg/mod.py": "X = 1\n"})
        (self.workspace / "pkg/mod.py").write_text("X = 1\nY = 2\nZ = 3\n")
        (self.workspace / "PLAN.md").write_text("# Plan\n")

        self.assertEqual(
            fixtures.changed_since_baseline(self.workspace),
            ["PLAN.md", "pkg/mod.py"],
        )
        self.assertEqual(fixtures.added_lines_since_baseline(self.workspace), 2)

    def test_baseline_diff_is_empty_for_an_untouched_workspace(self):
        fixtures.init_repo(self.workspace, {"pkg/mod.py": "X = 1\n"})
        fixtures.save_roadmap(self.workspace, models.Roadmap(goal="g"))

        self.assertEqual(fixtures.changed_since_baseline(self.workspace), [])
        self.assertEqual(fixtures.added_lines_since_baseline(self.workspace), 0)

    def test_roadmap_round_trip(self):
        fixtures.init_repo(self.workspace, {"pkg/mod.py": "X = 1\n"})

        roadmap = models.Roadmap(
            goal="Build a thing",
            tasks=[models.Task(id="task1", description="Do the thing")],
        )
        fixtures.save_roadmap(self.workspace, roadmap)
        loaded = fixtures.load_roadmap(self.workspace)

        self.assertEqual(loaded.goal, "Build a thing")
        self.assertEqual(loaded.tasks[0].id, "task1")


if __name__ == "__main__":
    unittest.main()
