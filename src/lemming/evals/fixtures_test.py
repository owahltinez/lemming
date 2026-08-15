import pathlib
import shutil
import tempfile
import unittest

from lemming import models
from lemming.evals import fixtures


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

        # The tasks file and lemming state are owned by the harness, not the
        # agent under eval, so they must not count as source drift.
        fixtures.save_roadmap(self.workspace, models.Roadmap(goal="g"))
        (self.workspace / ".lemming").mkdir()
        (self.workspace / ".lemming" / "state").write_text("x")
        (self.workspace / "runner.log").write_text("log")

        # Tool byproducts belong to whatever the agent ran, not to the
        # agent's judgement: a scenario that bans stray files must not go
        # red because pytest left a cache directory behind.
        (self.workspace / ".pytest_cache").mkdir()
        (self.workspace / ".pytest_cache" / "v").write_text("x")

        self.assertEqual(fixtures.dirty_paths(self.workspace), [])
        self.assertEqual(fixtures.changed_since_baseline(self.workspace), [])

    def test_changes_land_in_a_second_commit(self):
        # Hook scenarios review "the work the finished task left behind", so
        # that work has to exist as a real diff the agent can discover with
        # git rather than a prose description it has to take on faith.
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
        # An agent that commits its work leaves a clean git status, so a
        # grader reading only dirty_paths would score it as having done
        # nothing at all. Everything since the baseline has to count.
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
