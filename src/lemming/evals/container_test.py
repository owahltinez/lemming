import pathlib
import shutil
import tempfile
import unittest
import unittest.mock

from lemming.evals import container


class TestTrialCommand(unittest.TestCase):
    def setUp(self):
        self.workspace = pathlib.Path("/host/run/trial-0/workspace")
        self.home = pathlib.Path("/host/run/trial-0/home")

    def command(self, **kwargs):
        return container.trial_command(
            self.workspace, self.home, ["--task-id", "task1"], **kwargs
        )

    def test_mounts_workspace_and_home(self):
        command = self.command()

        self.assertIn(f"{self.workspace}:/workspace", command)
        self.assertIn(f"{self.home}:/lemming-home", command)
        self.assertIn("LEMMING_HOME=/lemming-home", command)

    def test_overrides_entrypoint_and_targets_trial_module(self):
        command = self.command(image="custom-image")

        entrypoint = command.index("--entrypoint")
        self.assertEqual(command[entrypoint + 1], "uv")
        image = command.index("custom-image")
        self.assertEqual(
            command[image + 1 :],
            [
                "run",
                "--project",
                "/opt/lemming",
                "python",
                "-m",
                "lemming.evals.trial",
                "--task-id",
                "task1",
            ],
        )

    def test_forwards_credentials_only_when_set(self):
        env = {"ANTHROPIC_API_KEY": "secret"}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            command = self.command()

        self.assertIn("ANTHROPIC_API_KEY", command)
        self.assertNotIn("secret", " ".join(command))
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", command)

    def test_extra_volumes_are_passed_through(self):
        spec = "/host/creds:/root/.config/agy:ro"
        command = self.command(volumes=(spec,))

        self.assertIn(spec, command)


class TestRunTrial(unittest.TestCase):
    def test_streams_output_to_log_file(self):
        run_dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, run_dir, ignore_errors=True)
        log_file = run_dir / "container.log"

        # Substituting `echo` for docker exercises the subprocess plumbing
        # (argv, log redirection, check=True) without a docker daemon.
        container.run_trial(
            pathlib.Path("/ws"),
            pathlib.Path("/home"),
            ["--task-id", "task1"],
            time_limit=1,
            log_file=log_file,
            docker="echo",
        )

        logged = log_file.read_text()
        self.assertIn("run --rm", logged)
        self.assertIn("lemming.evals.trial --task-id task1", logged)


if __name__ == "__main__":
    unittest.main()


class BuildImageTest(unittest.TestCase):
    """The build must not carry what the eval trials never use."""

    @unittest.mock.patch("subprocess.run")
    def test_builds_without_browsers(
        self, run: unittest.mock.MagicMock
    ) -> None:
        """Browsers are over half the image and no trial drives one."""
        container.build_image(pathlib.Path("/repo"))

        argv = run.call_args[0][0]
        self.assertIn("--build-arg", argv)
        self.assertIn("INSTALL_BROWSERS=false", argv)

    @unittest.mock.patch("subprocess.run")
    def test_prunes_the_build_cache(self, run: unittest.mock.MagicMock) -> None:
        """Repeated eval runs accumulate cache faster than images."""
        container.prune_build_cache()

        argv = run.call_args[0][0]
        self.assertEqual(argv[:3], ["docker", "builder", "prune"])
        self.assertIn("--force", argv)

    @unittest.mock.patch("subprocess.run", side_effect=OSError("no docker"))
    def test_pruning_never_fails_a_run(
        self, run: unittest.mock.MagicMock
    ) -> None:
        """Cleanup runs after the results are in; it must not lose them."""
        container.prune_build_cache()
