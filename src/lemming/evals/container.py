"""Docker-based isolation for eval trials.

Each trial runs in a throwaway container built from the repo Dockerfile,
which already ships the supported runner CLIs and a lemming install at
/opt/lemming. Only the trial workspace and a per-trial lemming home are
mounted, so the agent under eval cannot touch the host and concurrent
trials cannot interfere with each other through shared runner state.
"""

import os
import pathlib
import subprocess

DEFAULT_IMAGE = "lemming-evals"

# Credential variables forwarded into the container when set on the host.
DEFAULT_FORWARD_ENV = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)

# Paths inside the container; the harness mounts host dirs onto these.
WORKSPACE_MOUNT = "/workspace"
HOME_MOUNT = "/lemming-home"

# Extra seconds past the hook time limit before the container is killed.
TIMEOUT_GRACE_SECONDS = 300


def build_image(
    context: pathlib.Path,
    image: str = DEFAULT_IMAGE,
    docker: str = "docker",
) -> None:
    """Builds the eval image from the repo Dockerfile.

    Args:
        context: Repository root to use as the build context.
        image: Tag for the built image.
        docker: Docker-compatible CLI binary to invoke.
    """
    subprocess.run([docker, "build", "--tag", image, str(context)], check=True)


def trial_command(
    workspace: pathlib.Path,
    lemming_home: pathlib.Path,
    trial_args: list[str],
    image: str = DEFAULT_IMAGE,
    docker: str = "docker",
    forward_env: tuple[str, ...] = DEFAULT_FORWARD_ENV,
    volumes: tuple[str, ...] = (),
) -> list[str]:
    """Returns the docker argv that runs one trial in a container.

    Args:
        workspace: Host directory holding the fixture repo and tasks file.
        lemming_home: Host directory mounted as LEMMING_HOME so runner logs
            survive the container for debugging.
        trial_args: Arguments for python -m lemming.evals.trial, using
            container paths (the tasks file lives under WORKSPACE_MOUNT).
        image: Image tag to run.
        docker: Docker-compatible CLI binary to invoke.
        forward_env: Env var names forwarded when set on the host.
        volumes: Extra --volume specs (e.g. read-only credential mounts).

    Returns:
        The full docker run argv.
    """
    command = [
        docker,
        "run",
        "--rm",
        "--volume",
        f"{workspace}:{WORKSPACE_MOUNT}",
        "--volume",
        f"{lemming_home}:{HOME_MOUNT}",
        "--env",
        f"LEMMING_HOME={HOME_MOUNT}",
        "--workdir",
        WORKSPACE_MOUNT,
        "--entrypoint",
        "uv",
    ]

    # Bare --env NAME makes docker read the value from its own environment,
    # keeping secrets out of the argv.
    for name in forward_env:
        if os.environ.get(name):
            command.extend(["--env", name])
    for spec in volumes:
        command.extend(["--volume", spec])

    command.extend(
        [
            image,
            "run",
            "--project",
            "/opt/lemming",
            "python",
            "-m",
            "lemming.evals.trial",
            *trial_args,
        ]
    )
    return command


def run_trial(
    workspace: pathlib.Path,
    lemming_home: pathlib.Path,
    trial_args: list[str],
    time_limit: int,
    log_file: pathlib.Path,
    **command_kwargs,
) -> None:
    """Runs one containerized trial, streaming output to a log file.

    Args:
        workspace: Host directory holding the fixture repo and tasks file.
        lemming_home: Host directory mounted as LEMMING_HOME.
        trial_args: Arguments for python -m lemming.evals.trial.
        time_limit: Hook time limit in minutes, used to size the hard
            container timeout.
        log_file: File receiving the combined container output.
        **command_kwargs: Extra keyword arguments for trial_command.

    Raises:
        subprocess.CalledProcessError: If the container exits non-zero.
        subprocess.TimeoutExpired: If the container exceeds the timeout.
    """
    command = trial_command(
        workspace, lemming_home, trial_args, **command_kwargs
    )
    timeout = time_limit * 60 + TIMEOUT_GRACE_SECONDS
    with log_file.open("w") as log:
        subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=True,
        )
