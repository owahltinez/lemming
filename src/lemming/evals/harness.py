"""Parallel execution of eval scenarios with per-trial isolation.

Every (scenario, trial) pair gets its own workspace and lemming home under
the run directory, so trials can run concurrently without sharing any
state. The actual trial execution is injectable: production uses the
docker-based runner from the container module, tests use in-process fakes.
"""

import concurrent.futures
import dataclasses
import pathlib
import shutil
import time
import traceback
import typing

from .. import models
from . import container, fixtures, scenarios

# Entries under the agy home that are caches, logs, or user data the trials
# must not see. Functional state stays, notably the auth token under
# antigravity-cli/, which agy requires to start at all.
_AGY_HOME_EXCLUDES = ("tmp", "history", "conversations", "log", "scratch")


@dataclasses.dataclass(frozen=True)
class HarnessConfig:
    """Knobs for a harness run.

    Attributes:
        runner: Runner CLI driven inside the container (e.g. "agy").
        trials: Number of times each scenario is attempted.
        jobs: Maximum trials running concurrently.
        time_limit: Time limit in minutes for each hook run.
        image: Container image tag to run trials in.
        docker: Docker-compatible CLI binary to invoke.
        volumes: Extra --volume specs forwarded to docker run.
    """

    runner: str = "agy"
    trials: int = 3
    jobs: int = 4
    time_limit: int = 15
    image: str = container.DEFAULT_IMAGE
    docker: str = "docker"
    volumes: tuple[str, ...] = ()


@dataclasses.dataclass
class TrialResult:
    """Outcome of a single graded trial."""

    scenario: str
    trial: int
    passed: bool
    checks: list[scenarios.Check]
    duration: float
    workspace: pathlib.Path
    error: str = ""


# A trial runner takes (scenario, workspace, lemming_home, config) and
# raises on infrastructure failure; grading happens afterwards regardless.
TrialRunner = typing.Callable[
    [scenarios.Scenario, pathlib.Path, pathlib.Path, HarnessConfig], None
]


def _trial_args(
    scenario: scenarios.Scenario, config: HarnessConfig
) -> list[str]:
    """Builds the in-container trial argv for a scenario."""
    tasks_file = f"{container.WORKSPACE_MOUNT}/{fixtures.TASKS_FILE_NAME}"
    outcome = (
        "failed"
        if scenario.outcome == models.TaskStatus.FAILED
        else "completed"
    )
    return [
        "--tasks-file",
        tasks_file,
        "--task-id",
        scenario.task_id,
        "--hook",
        scenario.hook,
        "--outcome",
        outcome,
        "--runner",
        config.runner,
        "--time-limit",
        str(config.time_limit),
    ]


def _prepare_agy_home(
    host_home: pathlib.Path, trial_dir: pathlib.Path
) -> str | None:
    """Copies the host agy config into the trial for a private mount.

    agy authenticates via files under ~/.gemini and writes back to them
    (token refresh, state), so a read-only mount would break it and a
    shared read-write mount would let concurrent yolo-mode trials mutate
    the host's real state. Each trial instead gets its own disposable copy.

    Args:
        host_home: The agy home on the host (normally ~/.gemini).
        trial_dir: The trial directory receiving the copy.

    Returns:
        A docker --volume spec for the copy, or None when the host has no
        agy home to copy.
    """
    if not host_home.is_dir():
        return None
    target = trial_dir / "agy-home"
    shutil.copytree(
        host_home,
        target,
        ignore=shutil.ignore_patterns(*_AGY_HOME_EXCLUDES),
        ignore_dangling_symlinks=True,
    )
    return f"{target}:/root/.gemini"


def _run_trial_in_container(
    scenario: scenarios.Scenario,
    workspace: pathlib.Path,
    lemming_home: pathlib.Path,
    config: HarnessConfig,
) -> None:
    """Default trial runner: executes the trial in a docker container."""
    volumes = config.volumes
    if config.runner.startswith("agy"):
        agy_volume = _prepare_agy_home(
            pathlib.Path.home() / ".gemini", workspace.parent
        )
        if agy_volume:
            volumes = (*volumes, agy_volume)

    container.run_trial(
        workspace,
        lemming_home,
        _trial_args(scenario, config),
        time_limit=config.time_limit,
        log_file=workspace.parent / "container.log",
        image=config.image,
        docker=config.docker,
        volumes=volumes,
    )


def _execute_trial(
    scenario: scenarios.Scenario,
    trial_index: int,
    run_dir: pathlib.Path,
    config: HarnessConfig,
    run_trial_fn: TrialRunner,
) -> TrialResult:
    """Builds, runs, and grades one isolated trial."""
    trial_dir = run_dir / scenario.name / f"trial-{trial_index}"
    workspace = trial_dir / "workspace"
    lemming_home = trial_dir / "home"
    lemming_home.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    error = ""
    try:
        scenario.build(workspace)
        run_trial_fn(scenario, workspace, lemming_home, config)
    except Exception:
        error = traceback.format_exc(limit=5)

    # Grade the workspace even after infrastructure errors: the checks
    # document exactly what state the trial left behind.
    try:
        checks = scenario.grade(workspace)
    except Exception:
        checks = [
            scenarios.Check(
                name="grading",
                passed=False,
                detail=traceback.format_exc(limit=5),
            )
        ]

    return TrialResult(
        scenario=scenario.name,
        trial=trial_index,
        passed=not error and scenarios.passed(checks),
        checks=checks,
        duration=time.monotonic() - started,
        workspace=workspace,
        error=error,
    )


def run_suite(
    suite: list[scenarios.Scenario],
    run_dir: pathlib.Path,
    config: HarnessConfig,
    run_trial_fn: TrialRunner | None = None,
) -> list[TrialResult]:
    """Runs every scenario in a suite for the configured number of trials.

    Args:
        suite: Scenarios to evaluate.
        run_dir: Directory receiving one subdirectory per trial.
        config: Harness configuration.
        run_trial_fn: Trial executor override; defaults to the docker
            runner.

    Returns:
        All trial results, ordered by scenario then trial index.
    """
    executor_fn = run_trial_fn or _run_trial_in_container
    jobs = max(1, config.jobs)

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [
            pool.submit(
                _execute_trial, scenario, index, run_dir, config, executor_fn
            )
            for scenario in suite
            for index in range(config.trials)
        ]
        results = [future.result() for future in futures]

    return sorted(results, key=lambda r: (r.scenario, r.trial))


def summarize(
    results: list[TrialResult],
) -> dict[str, tuple[int, int]]:
    """Aggregates trial results into per-scenario (passed, total) counts."""
    totals: dict[str, tuple[int, int]] = {}
    for result in results:
        passed, total = totals.get(result.scenario, (0, 0))
        totals[result.scenario] = (passed + int(result.passed), total + 1)
    return totals
