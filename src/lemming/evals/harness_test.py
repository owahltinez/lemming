import dataclasses
import json
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


class TestRunnerFailureClassification(HarnessTestCase):
    def runner_writing_result(self, payload: dict):
        def run_trial_fn(scenario, workspace, lemming_home, config):
            path = lemming_home / harness.RESULT_FILE_NAME
            path.write_text(json.dumps(payload))
            raise RuntimeError("Hook runner failed")

        return run_trial_fn

    def test_reports_a_runner_that_never_started_as_infra(self):
        # The agent under eval never got to make a decision, so this must
        # not be counted against its judgement.
        results = harness.run_suite(
            [_scenario("fast-exit-healthy")],
            self.run_dir,
            self.config,
            self.runner_writing_result(
                {
                    "exit_codes": {"roadmap": -1},
                    "launch_failed": True,
                    "timed_out": False,
                }
            ),
        )

        for result in results:
            self.assertFalse(result.passed)
            self.assertTrue(result.infra_failure)
            self.assertTrue(result.launch_failed)

    def test_reports_a_timeout_as_infra(self):
        results = harness.run_suite(
            [_scenario("fast-exit-healthy")],
            self.run_dir,
            self.config,
            self.runner_writing_result(
                {
                    "exit_codes": {"roadmap": -14},
                    "launch_failed": False,
                    "timed_out": True,
                }
            ),
        )

        for result in results:
            self.assertTrue(result.infra_failure)
            self.assertTrue(result.timed_out)

    def test_a_misbehaving_agent_is_not_an_infra_failure(self):
        results = harness.run_suite(
            [_scenario("extend-goal-unmet")],
            self.run_dir,
            self.config,
            _finalizing_runner,
        )

        for result in results:
            self.assertFalse(result.passed)
            self.assertFalse(result.infra_failure)


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

    def test_points_the_result_file_at_the_lemming_home_mount(self):
        args = harness._trial_args(_scenario("fast-exit-healthy"), self.config)

        self.assertEqual(
            args[args.index("--result-file") + 1],
            f"/lemming-home/{harness.RESULT_FILE_NAME}",
        )

    def test_a_task_scenario_sends_its_prompt_and_workspace(self):
        # A task scenario has no fixture roadmap to point at: the agent is
        # given a prompt and the workspace mount it works in.
        scenario = scenarios.Scenario(
            name="task-demo",
            summary="Writes the code the prompt asks for.",
            build=lambda workspace: None,
            grade=lambda workspace: [],
            mode="task",
            prompt="Add divide.",
        )

        args = harness._trial_args(scenario, self.config)

        self.assertEqual(args[args.index("--mode") + 1], "task")
        self.assertEqual(args[args.index("--workspace") + 1], "/workspace")
        self.assertEqual(args[args.index("--prompt") + 1], "Add divide.")
        self.assertEqual(args[args.index("--runner") + 1], "agy")
        self.assertNotIn("--hook", args)
        self.assertNotIn("--task-id", args)


class TestRunnerHomes(HarnessTestCase):
    def fake_home(self) -> pathlib.Path:
        """Builds a host home carrying config for both runners."""
        home = self.run_dir / "host"
        gemini = home / ".gemini"
        (gemini / "tmp").mkdir(parents=True)
        (gemini / "tmp" / "cache.bin").write_text("cache")
        (gemini / "conversations").mkdir()
        (gemini / "conversations" / "chat.jsonl").write_text("private")
        (gemini / "GEMINI.md").write_text("global instructions")
        (gemini / "antigravity-cli" / "brain").mkdir(parents=True)
        (gemini / "antigravity-cli" / "brain" / "index.bin").write_text("big")
        (gemini / "antigravity-cli" / "bin").mkdir()
        (gemini / "antigravity-cli" / "bin" / "agy").write_text("binary")
        (gemini / "antigravity-cli" / "antigravity-oauth-token").write_text(
            "tok"
        )
        (gemini / "extensions" / "ext").mkdir(parents=True)
        (gemini / "extensions" / "ext" / "manifest.json").write_text("{}")
        (gemini / "skills" / "skill").mkdir(parents=True)
        (gemini / "skills" / "skill" / "SKILL.md").write_text("skill")
        (gemini / "config" / "bin").mkdir(parents=True)
        (gemini / "config" / "bin" / "helper").write_text("keep")
        opencode = home / ".config" / "opencode"
        (opencode / "node_modules" / "pkg").mkdir(parents=True)
        (opencode / "node_modules" / "pkg" / "index.js").write_text("x")
        (opencode / "opencode.jsonc").write_text("{}")
        (opencode / "AGENTS.md").write_text("global instructions")
        return home

    def test_opencode_gets_its_own_private_config_copy(self):
        # agy trials run with the host's global instructions and skills. An
        # opencode arm running from a bare container would be compared
        # against a differently-equipped agent, not a different harness.
        trial_dir = self.run_dir / "trial-0"
        trial_dir.mkdir()

        specs = harness._prepare_runner_home(
            "opencode", trial_dir, home=self.fake_home()
        )

        copy = trial_dir / "opencode-home"
        self.assertEqual(list(specs), [f"{copy}:/root/.config/opencode"])
        self.assertEqual((copy / "opencode.jsonc").read_text(), "{}")
        self.assertEqual(
            (copy / "AGENTS.md").read_text(), "global instructions"
        )

    def test_agy_bulk_directories_are_not_copied_per_trial(self):
        # The agy home carries a multi-hundred-megabyte model cache and a
        # copy of the CLI itself. The container installs its own agy, and
        # copying that bulk once per trial is what turns a long run into
        # tens of gigabytes of disk churn.
        trial_dir = self.run_dir / "trial-0"
        trial_dir.mkdir()

        harness._prepare_runner_home("agy", trial_dir, home=self.fake_home())

        copy = trial_dir / "agy-home"
        self.assertFalse((copy / "antigravity-cli" / "brain").exists())
        self.assertFalse((copy / "antigravity-cli" / "bin").exists())
        # Only those exact paths: an unrelated bin/ elsewhere in the tree
        # is not bulk, and dropping it by name would be too broad.
        self.assertTrue((copy / "config" / "bin").is_dir())
        self.assertEqual(
            (copy / "antigravity-cli" / "antigravity-oauth-token").read_text(),
            "tok",
        )

    def test_agy_capabilities_opencode_lacks_are_left_behind(self):
        # Skills and extensions have no opencode counterpart. Copying them
        # would compare an agent with extra tooling against one without,
        # which measures equipment rather than the runners themselves.
        trial_dir = self.run_dir / "trial-0"
        trial_dir.mkdir()

        harness._prepare_runner_home("agy", trial_dir, home=self.fake_home())

        copy = trial_dir / "agy-home"
        self.assertFalse((copy / "skills").exists())
        self.assertFalse((copy / "extensions").exists())
        # The shared global instructions are the context both arms keep.
        self.assertEqual(
            (copy / "GEMINI.md").read_text(), "global instructions"
        )

    def test_installed_packages_are_not_copied_per_trial(self):
        # opencode's config directory carries a node_modules of tens of
        # megabytes. Copying it for every trial is pure disk churn, and a
        # long run is exactly where that starts to hurt.
        trial_dir = self.run_dir / "trial-0"
        trial_dir.mkdir()

        harness._prepare_runner_home(
            "opencode", trial_dir, home=self.fake_home()
        )

        copy = trial_dir / "opencode-home"
        self.assertFalse((copy / "node_modules").exists())

    def test_agy_keeps_auth_and_instructions_but_not_caches(self):
        trial_dir = self.run_dir / "trial-0"
        trial_dir.mkdir()

        specs = harness._prepare_runner_home(
            "agy", trial_dir, home=self.fake_home()
        )

        copy = trial_dir / "agy-home"
        self.assertEqual(list(specs), [f"{copy}:/root/.gemini"])
        self.assertEqual(
            (copy / "GEMINI.md").read_text(), "global instructions"
        )
        self.assertEqual(
            (copy / "antigravity-cli" / "antigravity-oauth-token").read_text(),
            "tok",
        )
        self.assertFalse((copy / "tmp").exists())
        self.assertFalse((copy / "conversations").exists())

    def test_returns_nothing_without_host_home(self):
        specs = harness._prepare_runner_home(
            "agy", self.run_dir, home=self.run_dir / "missing"
        )

        self.assertEqual(specs, ())

    def test_runner_string_with_arguments_still_matches(self):
        trial_dir = self.run_dir / "trial-0"
        trial_dir.mkdir()

        specs = harness._prepare_runner_home(
            "opencode --variant high", trial_dir, home=self.fake_home()
        )

        self.assertTrue(specs)

    def test_unknown_runner_gets_nothing(self):
        trial_dir = self.run_dir / "trial-0"
        trial_dir.mkdir()

        specs = harness._prepare_runner_home(
            "claude", trial_dir, home=self.fake_home()
        )

        self.assertEqual(specs, ())


class TestContainerRunnerVolumes(HarnessTestCase):
    def run_and_capture_volumes(self, config: harness.HarnessConfig):
        scenario = _scenario("fast-exit-healthy")
        workspace = self.run_dir / "trial-0" / "workspace"
        workspace.mkdir(parents=True)

        # Point the runner at a fake host home so the assertion does not
        # depend on whether the machine running the tests has agy installed.
        home = self.run_dir / "host"
        (home / ".gemini").mkdir(parents=True)

        with (
            unittest.mock.patch.object(
                harness.container, "run_trial"
            ) as run_trial,
            unittest.mock.patch.object(
                harness.pathlib.Path, "home", return_value=home
            ),
        ):
            harness._run_trial_in_container(
                scenario, workspace, workspace.parent / "home", config
            )
        return run_trial.call_args.kwargs["volumes"]

    def test_runner_home_is_mounted(self):
        volumes = self.run_and_capture_volumes(self.config)

        self.assertTrue(any(":/root/.gemini" in spec for spec in volumes))

    def test_runner_without_a_known_home_adds_no_mount(self):
        config = dataclasses.replace(self.config, runner="claude")

        self.assertEqual(self.run_and_capture_volumes(config), ())


if __name__ == "__main__":
    unittest.main()
