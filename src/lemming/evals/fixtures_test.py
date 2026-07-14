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

        self.assertEqual(fixtures.dirty_paths(self.workspace), [])

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
