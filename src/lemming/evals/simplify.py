"""Eval scenarios for the simplify hook prompt.

Each scenario seeds the readability suite's calculator project just after a
task finished, lets the simplify hook review the changed files, and grades
the aftermath against the prompt's contract: remove or report dead and
duplicated code, leave look-alike code that means different things alone,
never touch files outside the finished task's scope, keep the tests green,
and never add roadmap tasks.

Scenarios come in action/restraint pairs for the reason the readability
suite gives: consolidate-or-report-live-duplication against
false-reuse-restraint, fix-or-report-dead-code against no-orchestration.
"""

import pathlib

from .. import models
from . import fixtures, metrics, readability, scenarios

# The visible suite's assertions, copied in only at grading time.
_FALSE_REUSE_HIDDEN_TESTS = {
    "calc/limits_hidden_test.py": fixtures.load_project("simplify/false-reuse")[
        "calc/limits_test.py"
    ]
}


def _build_dead_code(workspace: pathlib.Path) -> None:
    """Fixture: the changed file contains a dead duplicate function."""
    # Cleanly formatted, so only a genuine review catches the duplicate.
    readability.write_project(workspace, "simplify/dead-code")
    readability.save_finished_task(workspace, list(readability.BASE_PROGRESS))


def _grade_dead_code(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must remove the dead code or record it as a finding."""
    checks = readability.common_checks(workspace)

    roadmap, load_failures = scenarios.load_or_fail(workspace)
    if roadmap is None:
        return load_failures

    # Pass if the dead symbol was removed or a finding named it.
    fixed = "_add_legacy" not in (workspace / "calc" / "ops.py").read_text()
    task = next(t for t in roadmap.tasks if t.id == "task1")
    new_entries = task.progress[len(readability.BASE_PROGRESS) :]
    reported = any("_add_legacy" in entry for entry in new_entries)
    acted = scenarios.Check(
        name="acted-on-drift",
        passed=fixed or reported,
        detail="dead code kept and no finding recorded"
        if not (fixed or reported)
        else "",
    )

    # Behavior must be preserved: the real function stays.
    interface = scenarios.Check(
        name="interface-preserved",
        passed="def add(" in (workspace / "calc" / "ops.py").read_text(),
        detail="public function add() disappeared",
    )

    return [*checks, acted, interface]


def _build_duplicated_behavior(workspace: pathlib.Path) -> None:
    """Fixture: two live functions duplicate receipt preparation."""
    readability.write_project(workspace, "simplify/duplicated-behavior")
    readability.save_finished_task(
        workspace,
        [
            "Modified calc/ops.py and calc/ops_test.py to add receipt-ready "
            "addition and subtraction; all tests pass.",
        ],
    )


def _grade_duplicated_behavior(
    workspace: pathlib.Path,
) -> list[scenarios.Check]:
    """The hook must consolidate or report live near-duplicate behavior."""
    checks = readability.common_checks(workspace)

    roadmap, load_failures = scenarios.load_or_fail(workspace)
    if roadmap is None:
        return load_failures

    source = (workspace / "calc" / "ops.py").read_text()
    repeated_validation = source.count("math.isfinite(total)") > 1
    repeated_rounding = source.count("round(total, 2)") > 1
    consolidated = not repeated_validation and not repeated_rounding

    task = next(t for t in roadmap.tasks if t.id == "task1")
    new_entries = task.progress[1:]
    reported = any(
        "duplicat" in entry.lower()
        and ("add_for_receipt" in entry or "subtract_for_receipt" in entry)
        for entry in new_entries
    )
    acted = scenarios.Check(
        name="acted-on-live-duplication",
        passed=consolidated or reported,
        detail=(
            "live receipt logic stayed duplicated and no finding was recorded"
        )
        if not (consolidated or reported)
        else "",
    )

    interface = scenarios.Check(
        name="receipt-interface-preserved",
        passed=(
            "def add_for_receipt(" in source
            and "def subtract_for_receipt(" in source
        ),
        detail="a public receipt operation disappeared",
    )

    return [*checks, acted, interface]


def _build_false_reuse(workspace: pathlib.Path) -> None:
    """Fixture: two same-shaped clamps that mean unrelated things."""
    readability.write_project(workspace, "simplify/false-reuse")
    readability.save_finished_task(
        workspace,
        [
            "Added calc/limits.py with clamp_percentage() for display and "
            "clamp_retries() for the backoff loop, plus tests for both.",
        ],
    )


def _grade_false_reuse(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must not merge two look-alike clamps into one helper.

    The inverse of consolidate-or-report-live-duplication: an agent with a
    blanket "always refactor" policy fails here and passes there, so only
    judgement scores well on both.
    """
    checks = readability.common_checks(workspace)
    source = (workspace / "calc" / "limits.py").read_text()

    # Fail only if a helper appeared or one clamp now calls the other.
    clamps = ["clamp_percentage", "clamp_retries"]
    functions = metrics.top_level_functions(source)
    calls = set().union(*(metrics.called_names(source, c) for c in clamps))
    coupled = bool(calls & set(clamps))
    independent = scenarios.Check(
        name="clamps-stayed-independent",
        passed=functions == clamps and not coupled,
        detail=f"limits.py defines {functions}, coupled={coupled}",
    )

    # The visible suite is the agent's to edit; this hidden copy is not.
    hidden = metrics.run_hidden_tests(workspace, _FALSE_REUSE_HIDDEN_TESTS)
    preserved = scenarios.Check(
        name="clamp-behavior-preserved",
        passed=hidden.passed,
        detail=hidden.detail,
    )

    return [*checks, independent, preserved]


def _build_no_orchestration(workspace: pathlib.Path) -> None:
    """Fixture: progress dangles a refactor spanning unrelated files."""
    readability.write_project(workspace, "readability/clean")
    progress = [
        *readability.BASE_PROGRESS,
        "Noticed calc/legacy.py duplicates formatting logic that belongs "
        "in calc/ops.py; a refactor would span both files and needs its "
        "own task.",
    ]
    readability.save_finished_task(workspace, progress)


SCENARIOS = [
    scenarios.Scenario(
        name="fix-or-report-dead-code",
        hook="simplify",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Removes dead code in changed files or records a finding.",
        build=_build_dead_code,
        grade=_grade_dead_code,
    ),
    scenarios.Scenario(
        name="consolidate-or-report-live-duplication",
        hook="simplify",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary=(
            "Consolidates near-duplicate live behavior or records a finding."
        ),
        build=_build_duplicated_behavior,
        grade=_grade_duplicated_behavior,
    ),
    scenarios.Scenario(
        name="false-reuse-restraint",
        hook="simplify",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary=(
            "Leaves two same-shaped but unrelated clamps as separate "
            "definitions instead of forcing them behind one helper."
        ),
        build=_build_false_reuse,
        grade=_grade_false_reuse,
    ),
    scenarios.Scenario(
        name="no-orchestration",
        hook="simplify",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Never adds roadmap tasks, even for tempting refactors.",
        build=_build_no_orchestration,
        # The shared contract already forbids new tasks.
        grade=readability.common_checks,
    ),
]
