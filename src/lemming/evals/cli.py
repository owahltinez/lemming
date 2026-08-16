"""Command-line interface for the containerized prompt eval harness."""

import dataclasses
import datetime
import json
import pathlib
import sys
import tempfile

import click

from . import container, harness, report, scenarios, suites

# Repo root when running from a source checkout (src/lemming/evals/cli.py).
_DEFAULT_CONTEXT = pathlib.Path(__file__).resolve().parents[3]


@click.group()
def cli() -> None:
    """Containerized prompt evals for lemming's agents and hooks."""


@cli.command("list")
def list_scenarios() -> None:
    """Lists every registered eval suite and its scenarios."""
    for suite_name, suite in suites.all_suites().items():
        click.secho(suite_name, bold=True)
        for scenario in suite:
            click.echo(f"  {scenario.name}: {scenario.summary}")


def _select_suite(
    suite_name: str, scenario_names: tuple[str, ...]
) -> list[scenarios.Scenario]:
    """Resolves and validates the scenarios to run."""
    registry = suites.all_suites()
    if suite_name not in registry:
        raise click.UsageError(
            f"Unknown suite '{suite_name}'. Available: {sorted(registry)}"
        )
    suite = registry[suite_name]
    if not scenario_names:
        return suite

    by_name = {scenario.name: scenario for scenario in suite}
    unknown = [name for name in scenario_names if name not in by_name]
    if unknown:
        raise click.UsageError(
            f"Unknown scenarios {unknown}. Available: {sorted(by_name)}"
        )
    return [by_name[name] for name in scenario_names]


def _report(results: list[harness.TrialResult], min_pass_rate: float) -> bool:
    """Prints per-scenario pass rates and returns overall success."""
    success = True
    for scenario_name, (passed, total) in harness.summarize(results).items():
        rate = passed / total if total else 0.0
        ok = rate >= min_pass_rate
        success = success and ok
        color = "green" if ok else "red"
        click.secho(f"{scenario_name}: {passed}/{total}", fg=color, bold=True)

        # Call out trials where the agent never got to make a decision.
        infra = sum(
            1
            for result in results
            if result.scenario == scenario_name and result.infra_failure
        )
        if infra:
            click.secho(
                f"  {infra} infra failure(s): runner never started or "
                "timed out",
                fg="yellow",
            )

        # Show each trial's failing checks and workspace, advisory reds
        # included: they flag a trial for inspection without failing it.
        for result in results:
            if result.scenario != scenario_name:
                continue
            for check in result.checks:
                if check.passed:
                    continue
                detail = f" ({check.detail})" if check.detail else ""
                if check.advisory:
                    click.secho(
                        f"  trial-{result.trial} inspect: {check.name}{detail}",
                        fg="yellow",
                    )
                elif not result.passed:
                    click.echo(f"  trial-{result.trial} {check.name}{detail}")
            if result.passed:
                continue
            if result.error:
                click.echo(
                    f"  trial-{result.trial} infra error: "
                    f"{result.error.strip().splitlines()[-1]}"
                )
            click.echo(f"  trial-{result.trial} workspace: {result.workspace}")
    return success


def _write_json_report(
    results: list[harness.TrialResult],
    path: pathlib.Path,
    config: harness.HarnessConfig,
    suite_name: str,
) -> None:
    """Writes the trial results and how the run was configured.

    Two reports from a comparison are indistinguishable without the
    configuration, and a run costs hours of wall clock to reproduce.

    Args:
        results: Graded trials to record.
        path: File to write.
        config: The harness configuration the run used.
        suite_name: Name of the suite that was run.
    """
    # asdict drops infra_failure, a derived property, so add it back.
    payload = {
        "config": {
            **dataclasses.asdict(config),
            "suite": suite_name,
            "started_at": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
        },
        "results": [
            {
                **dataclasses.asdict(result),
                "workspace": str(result.workspace),
                "infra_failure": result.infra_failure,
            }
            for result in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2))


@cli.command()
@click.option("--suite", "suite_name", default="roadmap", show_default=True)
@click.option(
    "--scenario",
    "scenario_names",
    multiple=True,
    help="Run only these scenarios (repeatable).",
)
@click.option("--trials", default=3, show_default=True)
@click.option("--jobs", default=4, show_default=True)
@click.option("--runner", default="agy", show_default=True)
@click.option(
    "--time-limit",
    default=10,
    show_default=True,
    help="Per-trial hook time limit in minutes.",
)
@click.option("--image", default=container.DEFAULT_IMAGE, show_default=True)
@click.option("--docker", default="docker", show_default=True)
@click.option(
    "--volume",
    "volumes",
    multiple=True,
    help="Extra docker --volume specs, e.g. credential mounts.",
)
@click.option(
    "--context",
    type=click.Path(path_type=pathlib.Path, exists=True),
    default=_DEFAULT_CONTEXT,
    help="Docker build context (repo root).",
)
@click.option(
    "--run-dir",
    type=click.Path(path_type=pathlib.Path),
    help="Directory for trial workspaces (default: a new temp dir).",
)
@click.option(
    "--skip-build",
    is_flag=True,
    help="Reuse the existing image instead of rebuilding it.",
)
@click.option(
    "--min-pass-rate",
    default=1.0,
    show_default=True,
    help="Minimum per-scenario pass rate for a zero exit code.",
)
@click.option(
    "--json-report",
    type=click.Path(path_type=pathlib.Path),
    help="Write full trial results to this JSON file.",
)
def run(
    suite_name: str,
    scenario_names: tuple[str, ...],
    trials: int,
    jobs: int,
    runner: str,
    time_limit: int,
    image: str,
    docker: str,
    volumes: tuple[str, ...],
    context: pathlib.Path,
    run_dir: pathlib.Path | None,
    skip_build: bool,
    min_pass_rate: float,
    json_report: pathlib.Path | None,
) -> None:
    """Runs an eval suite in parallel, isolated containers."""
    suite = _select_suite(suite_name, scenario_names)

    if not skip_build:
        if not (context / "Dockerfile").is_file():
            raise click.UsageError(
                f"No Dockerfile in build context {context}; pass --context."
            )
        click.echo(f"Building eval image '{image}'...")
        container.build_image(context, image=image, docker=docker)
        container.prune_build_cache(docker=docker)

    if run_dir is None:
        run_dir = pathlib.Path(tempfile.mkdtemp(prefix="lemming-evals-"))
    run_dir.mkdir(parents=True, exist_ok=True)

    config = harness.HarnessConfig(
        runner=runner,
        trials=trials,
        jobs=jobs,
        time_limit=time_limit,
        image=image,
        docker=docker,
        volumes=volumes,
    )
    total = len(suite) * trials
    click.echo(
        f"Running {len(suite)} scenario(s) x {trials} trial(s) "
        f"({total} containers, {jobs} at a time) under {run_dir}"
    )
    results = harness.run_suite(suite, run_dir, config)

    if json_report:
        _write_json_report(results, json_report, config, suite_name)
    if not _report(results, min_pass_rate):
        sys.exit(1)


def _format_rate(passed: int, total: int) -> str:
    """Renders a pass count with its rate and 95% interval."""
    if not total:
        return "-"
    low, high = report.wilson_interval(passed, total)
    return f"{passed}/{total} ({passed / total:.0%}) CI {low:.0%}-{high:.0%}"


@cli.command()
@click.argument("left", type=click.Path(path_type=pathlib.Path, exists=True))
@click.argument("right", type=click.Path(path_type=pathlib.Path, exists=True))
def compare(left: pathlib.Path, right: pathlib.Path) -> None:
    """Compares two eval reports scenario by scenario.

    Agents are stochastic and eval runs are small, so the interval matters
    more than the gap between two percentages: a comparison that cannot
    separate the arms has to say so rather than let the raw numbers read
    like a result.
    """
    arms = [report.summarize(report.load(path)) for path in (left, right)]
    for arm in arms:
        click.secho(arm.label, bold=True)

    degenerate = set(report.degenerate_scenarios(*arms))
    names = sorted(set(arms[0].by_scenario) | set(arms[1].by_scenario))
    width = max((len(name) for name in names), default=0)

    click.echo()
    for name in names:
        cells = []
        for arm in arms:
            passed, total = arm.by_scenario.get(name, (0, 0))
            cells.append(f"{passed}/{total}" if total else "-")
        note = "  degenerate" if name in degenerate else ""
        click.echo(f"{name:<{width}}  {cells[0]:>7}  {cells[1]:>7}{note}")

    click.echo()
    for arm in arms:
        click.echo(f"{arm.label}: {_format_rate(arm.passed, arm.total)}")
        click.echo(
            f"  infra failures: {arm.infra_failures}"
            f"   median trial: {arm.median_duration:.0f}s"
        )

    # Test the arms directly; overlapping intervals hide real differences.
    p_value = report.fisher_exact(
        arms[0].passed,
        arms[0].total - arms[0].passed,
        arms[1].passed,
        arms[1].total - arms[1].passed,
    )
    click.echo()
    if p_value < 0.05:
        click.secho(
            f"Arms differ: Fisher exact p={p_value:.3f}. The per-scenario "
            "counts are still directional at this many trials.",
            fg="green",
        )
    else:
        click.secho(
            f"No separation: Fisher exact p={p_value:.3f}. Consistent with "
            "the arms performing the same.",
            fg="yellow",
        )
    if degenerate:
        click.secho(
            f"{len(degenerate)} scenario(s) scored identically at an "
            "extreme in both arms and cannot discriminate.",
            fg="yellow",
        )
