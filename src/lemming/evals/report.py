"""Reading and summarizing eval reports for comparison between runners.

A single run's pass rates are only meaningful next to another run's, so the
statistics live here rather than in the command that prints them: comparing
two arms is what the numbers are for, and it needs to be testable without a
terminal.
"""

import dataclasses
import json
import math
import pathlib
import statistics

# 95% two-sided normal quantile, the interval the comparison is reported at.
_Z = 1.96


@dataclasses.dataclass(frozen=True)
class Report:
    """One eval run's results plus whatever is known about how it ran.

    Attributes:
        label: Human-readable name for the arm, for use in output.
        config: How the run was configured; empty for reports written
            before runs recorded their configuration.
        results: Raw trial records as written by the run.
    """

    label: str
    config: dict
    results: list[dict]


@dataclasses.dataclass(frozen=True)
class Summary:
    """Aggregated outcome of one arm.

    Attributes:
        label: The arm's name.
        by_scenario: Scenario name mapped to (passed, total).
        passed: Trials passed across every scenario.
        total: Trials run across every scenario.
        infra_failures: Trials whose runner never started or timed out.
        median_duration: Median trial wall clock in seconds.
    """

    label: str
    by_scenario: dict[str, tuple[int, int]]
    passed: int
    total: int
    infra_failures: int
    median_duration: float


def load(path: pathlib.Path) -> Report:
    """Reads a report, tolerating the shape written before config blocks.

    Args:
        path: Path of the JSON report to read.

    Returns:
        The parsed report. A bare list of trials is read as results with no
        configuration, so runs that predate config blocks stay usable.
    """
    payload = json.loads(path.read_text())
    if isinstance(payload, list):
        config, results = {}, payload
    else:
        config = payload.get("config", {})
        results = payload.get("results", [])

    return Report(
        label=config.get("runner") or path.stem,
        config=config,
        results=results,
    )


def wilson_interval(passed: int, total: int) -> tuple[float, float]:
    """Returns the 95% Wilson score interval for a pass rate.

    Wilson rather than the normal approximation because eval runs have few
    trials and rates near 0 or 1, where the normal interval is badly
    behaved and can leave the [0, 1] range entirely.

    Args:
        passed: Number of trials that passed.
        total: Number of trials run.

    Returns:
        Lower and upper bounds, clamped to [0, 1]. An empty run has no
        rate to bound and returns zeros.
    """
    if total == 0:
        return (0.0, 0.0)

    rate = passed / total
    denominator = 1 + _Z**2 / total
    centre = (rate + _Z**2 / (2 * total)) / denominator
    spread = _Z * math.sqrt(rate * (1 - rate) / total + _Z**2 / (4 * total**2))
    margin = spread / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def fisher_exact(
    left_passed: int,
    left_failed: int,
    right_passed: int,
    right_failed: int,
) -> float:
    """Returns the two-sided Fisher exact p-value for two arms.

    Whether two confidence intervals overlap is the wrong question to ask
    of two proportions: the test is conservative, so overlapping intervals
    routinely hide a real difference. Fisher is exact at the trial counts
    an eval run produces, where the normal approximations do not hold.

    Args:
        left_passed: Trials the first arm passed.
        left_failed: Trials the first arm failed.
        right_passed: Trials the second arm passed.
        right_failed: Trials the second arm failed.

    Returns:
        Probability of a split at least this extreme when the arms are
        equivalent. An empty table has nothing to test and returns 1.
    """
    left_total = left_passed + left_failed
    right_total = right_passed + right_failed
    passed = left_passed + right_passed
    total = left_total + right_total
    if not total or not left_total or not right_total:
        return 1.0

    def probability(top_left: int) -> float:
        return (
            math.comb(left_total, top_left)
            * math.comb(right_total, passed - top_left)
            / math.comb(total, passed)
        )

    observed = probability(left_passed)
    low = max(0, passed - right_total)
    high = min(left_total, passed)
    return min(
        1.0,
        sum(
            candidate
            for value in range(low, high + 1)
            if (candidate := probability(value)) <= observed + 1e-12
        ),
    )


def summarize(report: Report) -> Summary:
    """Aggregates one arm's trials into the figures a comparison needs.

    Args:
        report: The loaded run to aggregate.

    Returns:
        Per-scenario and pooled counts, infra failures, median duration.
    """
    # Infrastructure failures are recorded but not counted: a trial the
    # agent never got to influence is not evidence either way.
    by_scenario: dict[str, tuple[int, int]] = {}
    for trial in report.results:
        passed, total = by_scenario.setdefault(trial["scenario"], (0, 0))
        if _is_infra_failure(trial):
            continue
        by_scenario[trial["scenario"]] = (
            passed + int(trial["passed"]),
            total + 1,
        )

    durations = [trial["duration"] for trial in report.results]
    return Summary(
        label=report.label,
        by_scenario=by_scenario,
        passed=sum(passed for passed, _ in by_scenario.values()),
        total=sum(total for _, total in by_scenario.values()),
        infra_failures=sum(
            1 for trial in report.results if _is_infra_failure(trial)
        ),
        median_duration=statistics.median(durations) if durations else 0.0,
    )


def _is_infra_failure(trial: dict) -> bool:
    """Reports whether a trial failed before the agent made a decision."""
    return bool(
        trial.get("infra_failure")
        or trial.get("launch_failed")
        or trial.get("timed_out")
    )


def degenerate_scenarios(left: Summary, right: Summary) -> list[str]:
    """Returns scenarios that cannot separate the two arms.

    A scenario both arms pass every time, or fail every time, spends
    trials without contributing to the decision. Naming them is what makes
    a pilot worth running before a longer comparison.

    Args:
        left: Summary of the first arm.
        right: Summary of the second arm.

    Returns:
        Sorted names of scenarios where both arms scored identically at
        either extreme.
    """
    shared = set(left.by_scenario) & set(right.by_scenario)
    degenerate = []
    for name in shared:
        left_passed, left_total = left.by_scenario[name]
        right_passed, right_total = right.by_scenario[name]
        all_passed = left_passed == left_total and right_passed == right_total
        none_passed = left_passed == 0 and right_passed == 0
        if all_passed or none_passed:
            degenerate.append(name)
    return sorted(degenerate)
