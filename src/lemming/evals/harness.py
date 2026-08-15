"""Parallel execution of eval scenarios with per-trial isolation.

Every (scenario, trial) pair gets its own workspace and lemming home under
the run directory, so trials can run concurrently without sharing any
state. The actual trial execution is injectable: production uses the
docker-based runner from the container module, tests use in-process fakes.
"""

import concurrent.futures
import dataclasses
import json
import os
import pathlib
import shlex
import shutil
import time
import traceback
import typing

from .. import models
from . import container, fixtures, scenarios

# Entries under a runner home that are caches, logs, user data, or installed
# packages the trials must not see. Functional state stays, notably agy's auth
# token under antigravity-cli/, which agy requires to start at all.
# node_modules is tens of megabytes in opencode's config directory and would
# otherwise be copied once per trial.
_HOME_EXCLUDES = (
    "tmp",
    "history",
    "conversations",
    "log",
    "scratch",
    "node_modules",
)


@dataclasses.dataclass(frozen=True)
class _RunnerHome:
    """Where a runner keeps the configuration a trial should inherit.

    Attributes:
        host: Path of the config directory relative to the host home.
        mount: Absolute path the copy is mounted at inside the container.
        copy_name: Name of the per-trial copy under the trial directory.
        exclude_paths: Paths, relative to the config directory, that a
            trial must not inherit. Matched by exact path rather than by
            name so a small directory that happens to share a name is
            kept. Two reasons qualify: bulk that would be copied once per
            trial, and capabilities one runner has no counterpart for.
    """

    host: str
    mount: str
    copy_name: str
    exclude_paths: tuple[str, ...] = ()


# Comparing two runners is only meaningful when both bring the same context.
# Each runner therefore gets its own global instructions and auth, and
# anything one runner has no counterpart for is left behind: otherwise the
# result measures how the agents were equipped rather than how they behave.
_RUNNER_HOMES = {
    "agy": _RunnerHome(
        ".gemini",
        "/root/.gemini",
        "agy-home",
        exclude_paths=(
            # A model cache and a vendored copy of the CLI, together
            # hundreds of megabytes. The container installs its own agy,
            # so neither is needed and copying them per trial would
            # dominate the run's disk I/O.
            "antigravity-cli/brain",
            "antigravity-cli/bin",
            # Capabilities with no opencode counterpart. Leaving them in
            # would measure an agent that has extra tooling against one
            # that does not, which is not a comparison of runners.
            "skills",
            "extensions",
        ),
    ),
    "opencode": _RunnerHome(
        ".config/opencode", "/root/.config/opencode", "opencode-home"
    ),
}

# Written by the in-container trial into the per-trial lemming home, which
# is mounted from the host and so survives the container it was written in.
RESULT_FILE_NAME = "result.json"


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
    time_limit: int = 10
    image: str = container.DEFAULT_IMAGE
    docker: str = "docker"
    volumes: tuple[str, ...] = ()


@dataclasses.dataclass
class TrialResult:
    """Outcome of a single graded trial.

    Attributes:
        scenario: Name of the scenario the trial ran.
        trial: Zero-based index of the attempt.
        passed: Whether every non-advisory check held.
        checks: Graded assertions about the workspace.
        duration: Wall-clock seconds the trial took.
        workspace: Directory the trial ran in, kept for debugging.
        error: Traceback of an infrastructure error, if any.
        exit_codes: Per-hook exit codes reported by the trial.
        launch_failed: True when the runner never started.
        timed_out: True when the runner exceeded its time limit.
    """

    scenario: str
    trial: int
    passed: bool
    checks: list[scenarios.Check]
    duration: float
    workspace: pathlib.Path
    error: str = ""
    exit_codes: dict[str, int] = dataclasses.field(default_factory=dict)
    launch_failed: bool = False
    timed_out: bool = False

    @property
    def infra_failure(self) -> bool:
        """True when the trial failed without the agent making a decision.

        A runner that never started or ran out of time says nothing about
        the agent's judgement, and counting it as a behavioural failure
        penalizes whichever arm of a comparison is slower or flakier.
        """
        return self.launch_failed or self.timed_out


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
        "--result-file",
        f"{container.HOME_MOUNT}/{RESULT_FILE_NAME}",
    ]


def _ignore_excluded(
    source: pathlib.Path, runner_home: _RunnerHome
) -> typing.Callable[[str, list[str]], set[str]]:
    """Builds a copytree ignore callback for one runner's config directory.

    Caches are matched by name anywhere in the tree; bulk directories are
    matched by their exact path, so an unrelated directory that happens to
    share a name is still copied.

    Args:
        source: Root of the config directory being copied.
        runner_home: The runner's home description.

    Returns:
        A callable suitable for shutil.copytree's ignore argument.
    """
    by_name = shutil.ignore_patterns(*_HOME_EXCLUDES)

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(by_name(directory, names))
        relative = pathlib.Path(directory).relative_to(source)
        for name in names:
            if str(relative / name) in runner_home.exclude_paths:
                ignored.add(name)
        return ignored

    return ignore


def _prepare_runner_home(
    runner: str,
    trial_dir: pathlib.Path,
    home: pathlib.Path | None = None,
) -> tuple[str, ...]:
    """Copies a runner's host config into the trial for a private mount.

    Runners authenticate via files under their config directory and write
    back to them (token refresh, state), so a read-only mount would break
    them and a shared read-write mount would let concurrent yolo-mode
    trials mutate the host's real state. Each trial gets its own copy.

    Args:
        runner: The runner string; only its executable name is matched, so
            trailing arguments such as "--variant high" are tolerated.
        trial_dir: The trial directory receiving the copy.
        home: Host home directory, defaulting to the current user's.

    Returns:
        Docker --volume specs for the copy, empty when the runner has no
        known config directory or the host has nothing to copy.
    """
    home = home or pathlib.Path.home()
    executable = os.path.basename(shlex.split(runner)[0]) if runner else ""
    runner_home = next(
        (
            entry
            for name, entry in _RUNNER_HOMES.items()
            if executable.startswith(name)
        ),
        None,
    )
    if runner_home is None:
        return ()

    source = home / runner_home.host
    if not source.is_dir():
        return ()

    target = trial_dir / runner_home.copy_name
    shutil.copytree(
        source,
        target,
        ignore=_ignore_excluded(source, runner_home),
        ignore_dangling_symlinks=True,
    )
    return (f"{target}:{runner_home.mount}",)


def _run_trial_in_container(
    scenario: scenarios.Scenario,
    workspace: pathlib.Path,
    lemming_home: pathlib.Path,
    config: HarnessConfig,
) -> None:
    """Default trial runner: executes the trial in a docker container."""
    volumes = (
        *config.volumes,
        *_prepare_runner_home(config.runner, workspace.parent),
    )

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


def _read_result(path: pathlib.Path) -> dict:
    """Reads the trial's result record, tolerating a missing or bad file.

    A container that died before writing the record leaves nothing behind;
    that is itself an infrastructure failure, already captured as the
    trial's error, so an empty record is the honest answer here.

    Args:
        path: Host path of the record written inside the container.

    Returns:
        The parsed record, or an empty mapping when it is unusable.
    """
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


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

    record = _read_result(lemming_home / RESULT_FILE_NAME)
    return TrialResult(
        scenario=scenario.name,
        trial=trial_index,
        passed=not error and scenarios.passed(checks),
        checks=checks,
        duration=time.monotonic() - started,
        workspace=workspace,
        error=error,
        exit_codes=record.get("exit_codes", {}),
        launch_failed=bool(record.get("launch_failed")),
        timed_out=bool(record.get("timed_out")),
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
