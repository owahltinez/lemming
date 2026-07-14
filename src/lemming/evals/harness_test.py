import dataclasses
import pathlib
import shutil
import tempfile
import threading
import unittest
import unittest.mock

from lemming import models, tasks
from lemming.evals import fixtures, harness, roadmap, scenarios


def _scenario(name: str) -> scenarios.Scenario:
    return next(s for s in roadmap.SCENARIOS if s.name == name)


def _finalizing_runner(scenario, workspace, lemming_home, config):
    """Fake trial runner simulating a fast-exiting hook plus finalization."""
    tasks.update_task(
        fixtures.tasks_file(workspace),
        scenario.task_id,
        status=models.TaskStatus.COMPLETED,
        force=True,
    )


class HarnessTestCase(unittest.TestCase):
    def setUp(self):
        self.run_dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.run_dir, ignore_errors=True)
        self.config = harness.HarnessConfig(trials=2, jobs=2)


class TestRunSuite(HarnessTestCase):
    def test_grades_every_trial_in_isolated_workspaces(self):
        suite = [_scenario("fast-exit-healthy"), _scenario("extend-goal-unmet")]

        results = harness.run_suite(
            suite, self.run_dir, self.config, _finalizing_runner
        )

        self.assertEqual(len(results), 4)
        workspaces = {r.workspace for r in results}
        self.assertEqual(len(workspaces), 4)
        for result in results:
            self.assertTrue(result.workspace.is_dir())

        # A fast-exiting agent passes the healthy scenario but fails the
        # one that requires extending the roadmap.
        by_scenario = harness.summarize(results)
        self.assertEqual(by_scenario["fast-exit-healthy"], (2, 2))
        self.assertEqual(by_scenario["extend-goal-unmet"], (0, 2))

    def test_runs_trials_concurrently(self):
        active = threading.Semaphore(0)

        def blocking_runner(scenario, workspace, lemming_home, config):
            # Each trial waits for its peer: only concurrent execution can
            # release both semaphores without deadlocking the test.
            active.release()
            self.assertTrue(active.acquire(timeout=30))
            active.release()
            _finalizing_runner(scenario, workspace, lemming_home, config)

        results = harness.run_suite(
            [_scenario("fast-exit-healthy")],
            self.run_dir,
            self.config,
            blocking_runner,
        )

        self.assertEqual([r.passed for r in results], [True, True])

    def test_infrastructure_errors_are_captured(self):
        def broken_runner(scenario, workspace, lemming_home, config):
            raise RuntimeError("docker daemon unreachable")

        results = harness.run_suite(
            [_scenario("fast-exit-healthy")],
            self.run_dir,
            self.config,
            broken_runner,
        )

        for result in results:
            self.assertFalse(result.passed)
            self.assertIn("docker daemon unreachable", result.error)
            # The workspace is still graded to document the state left
            # behind by the failed trial.
            self.assertTrue(result.checks)


class TestTrialArgs(HarnessTestCase):
    def test_maps_scenario_to_container_paths(self):
        args = harness._trial_args(
            _scenario("repair-exhausted-failure"), self.config
        )

        self.assertEqual(
            args[args.index("--tasks-file") + 1], "/workspace/tasks.yml"
        )
        self.assertEqual(args[args.index("--outcome") + 1], "failed")
        self.assertEqual(args[args.index("--runner") + 1], "agy")


class TestAgyHome(HarnessTestCase):
    def fake_agy_home(self) -> pathlib.Path:
        home = self.run_dir / "host-gemini"
        (home / "tmp").mkdir(parents=True)
        (home / "tmp" / "cache.bin").write_text("cache")
        (home / "conversations").mkdir()
        (home / "conversations" / "chat.jsonl").write_text("private")
        (home / "config").mkdir()
        (home / "config" / "config.json").write_text("{}")
        (home / "antigravity-cli").mkdir()
        (home / "antigravity-cli" / "antigravity-oauth-token").write_text("tok")
        (home / "gemini-credentials.json").write_text('{"token": "t"}')
        return home

    def test_copies_auth_state_and_skips_caches(self):
        trial_dir = self.run_dir / "trial-0"
        trial_dir.mkdir()

        spec = harness._prepare_agy_home(self.fake_agy_home(), trial_dir)

        copy = trial_dir / "agy-home"
        self.assertEqual(spec, f"{copy}:/root/.gemini")
        self.assertEqual(
            (copy / "gemini-credentials.json").read_text(), '{"token": "t"}'
        )
        self.assertTrue((copy / "config" / "config.json").is_file())
        self.assertEqual(
            (copy / "antigravity-cli" / "antigravity-oauth-token").read_text(),
            "tok",
        )
        self.assertFalse((copy / "tmp").exists())
        self.assertFalse((copy / "conversations").exists())

    def test_returns_none_without_host_home(self):
        spec = harness._prepare_agy_home(self.run_dir / "missing", self.run_dir)

        self.assertIsNone(spec)

    def test_container_runner_mounts_private_agy_copy(self):
        scenario = _scenario("fast-exit-healthy")
        trial_dir = self.run_dir / "trial-0"
        workspace = trial_dir / "workspace"
        workspace.mkdir(parents=True)
        host_home = self.fake_agy_home()

        with (
            unittest.mock.patch.object(
                harness.container, "run_trial"
            ) as run_trial,
            unittest.mock.patch.object(
                harness.pathlib.Path, "home", return_value=host_home.parent
            ),
        ):
            # Point the runner at the fake host home (~/.gemini).
            host_home.rename(host_home.parent / ".gemini")
            harness._run_trial_in_container(
                scenario, workspace, trial_dir / "home", self.config
            )

        volumes = run_trial.call_args.kwargs["volumes"]
        self.assertEqual(
            list(volumes), [f"{trial_dir / 'agy-home'}:/root/.gemini"]
        )

    def test_non_agy_runner_adds_no_mount(self):
        scenario = _scenario("fast-exit-healthy")
        workspace = self.run_dir / "trial-0" / "workspace"
        workspace.mkdir(parents=True)
        config = dataclasses.replace(self.config, runner="claude")

        with unittest.mock.patch.object(
            harness.container, "run_trial"
        ) as run_trial:
            harness._run_trial_in_container(
                scenario, workspace, workspace.parent / "home", config
            )

        self.assertEqual(run_trial.call_args.kwargs["volumes"], ())


if __name__ == "__main__":
    unittest.main()
