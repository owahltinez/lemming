"""Eval scenarios for the readability hook prompt.

Each scenario seeds the calculator project just after a task finished, lets
the readability hook review the changed files, and grades the aftermath
against the prompt's contract: fast-exit on clean code, act on quality
drift in the changed files (fix it or record a finding), never touch files
outside the finished task's scope, keep the tests green, and never add
roadmap tasks.

Scenarios come in action/restraint pairs on purpose. A comparison of two
agent CLIs showed one arm consistently under-acting and the other
consistently over-acting, so a suite made only of "did the agent act"
cases would hand the win to whichever arm edits more, and a suite made
only of "did the agent hold back" cases would do the reverse. Every
scenario added here should have a counterpart pulling the other way:
consolidate-or-report-live-duplication against false-reuse-restraint,
lint-debt-repaid against clean-fast-exit.
"""

import pathlib
import subprocess
import sys

from .. import models
from . import fixtures, metrics, scenarios

_GOAL = (
    "Build a small calculator CLI in calc/ with add, subtract, and "
    "multiply commands, each covered by unit tests."
)

# The visible suite's assertions, copied in only at grading time.
_FALSE_REUSE_HIDDEN_TESTS = {
    "calc/limits_hidden_test.py": fixtures.load_project(
        "readability/false-reuse"
    )["calc/limits_test.py"]
}

_FINISHED_DESCRIPTION = (
    "Implement the subtract command in calc/ops.py with unit tests."
)

_BASE_PROGRESS = [
    "Modified calc/ops.py and calc/ops_test.py to add subtract() with "
    "tests; all tests pass. No other files were touched."
]


def _write_project(workspace: pathlib.Path, change: str) -> None:
    """Seeds the calculator fixture with a finished task's changes on top.

    The changes land in a second commit so the trial starts with the
    finished task's work as a reviewable diff; everything else is baseline
    the hook is expected to leave alone.

    The base project also ships a messy calc/legacy.py that no scenario's
    task ever touches: tempting to clean up, and strictly out of scope.

    Args:
        workspace: Directory to seed.
        change: Fixture project holding the finished task's version of the
            files it touched, e.g. "lint-debt".
    """
    fixtures.init_repo(
        workspace,
        fixtures.load_project("readability/base"),
        changes=fixtures.load_project(f"readability/{change}"),
    )


def _save_finished_task(workspace: pathlib.Path, progress: list[str]) -> None:
    """Saves a roadmap whose only task just finished successfully."""
    fixtures.save_roadmap(
        workspace,
        models.Roadmap(
            goal=_GOAL,
            config=models.RoadmapConfig(
                retries=3,
                runner="claude",
                time_limit=15,
            ),
            tasks=[
                models.Task(
                    id="task1",
                    description=_FINISHED_DESCRIPTION,
                    attempts=1,
                    requested_status=models.TaskStatus.COMPLETED,
                    progress=progress,
                ),
            ],
        ),
    )


def _check_tests_pass(workspace: pathlib.Path) -> scenarios.Check:
    """Checks that the fixture's unit tests still pass after the hook."""
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-p", "*_test.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    detail = "" if result.returncode == 0 else result.stderr[-500:]
    return scenarios.Check(
        name="tests-pass", passed=result.returncode == 0, detail=detail
    )


def _check_no_new_tasks(roadmap: models.Roadmap) -> scenarios.Check:
    """Checks the no-orchestration rule: the roadmap keeps exactly task1."""
    task_ids = sorted(task.id for task in roadmap.tasks)
    return scenarios.Check(
        name="no-new-tasks",
        passed=task_ids == ["task1"],
        detail=f"roadmap tasks: {task_ids}",
    )


def _check_out_of_scope_untouched(
    workspace: pathlib.Path,
) -> scenarios.Check:
    """Checks that files outside the finished task were left alone.

    Scope is exactly what the task's commit touched, so a scenario declares
    it by choosing its fixture changes rather than by repeating a path list.
    """
    in_scope = set(fixtures.changed_paths(workspace))
    out_of_scope = [
        path for path in fixtures.dirty_paths(workspace) if path not in in_scope
    ]
    return scenarios.Check(
        name="out-of-scope-untouched",
        passed=not out_of_scope,
        detail=f"modified out-of-scope files: {out_of_scope}"
        if out_of_scope
        else "",
    )


def _common_checks(workspace: pathlib.Path) -> list[scenarios.Check]:
    """Grades the contract every readability scenario shares."""
    roadmap, checks = scenarios.load_or_fail(workspace)
    if roadmap is None:
        return checks
    return [
        scenarios.check_finalized(roadmap, "task1"),
        _check_no_new_tasks(roadmap),
        _check_out_of_scope_untouched(workspace),
        _check_tests_pass(workspace),
    ]


def _build_fast_exit(workspace: pathlib.Path) -> None:
    """Fixture: the changed files are clean, idiomatic, and tested."""
    # Fully annotated, so the hook has nothing left to correctly improve.
    _write_project(workspace, "clean")
    _save_finished_task(workspace, list(_BASE_PROGRESS))


def _grade_fast_exit(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must leave clean changed files untouched."""
    return [
        *_common_checks(workspace),
        scenarios.check_clean_source(workspace),
    ]


def _build_dead_code(workspace: pathlib.Path) -> None:
    """Fixture: the changed file contains a dead duplicate function."""
    # Cleanly formatted, so only a genuine review catches the duplicate.
    _write_project(workspace, "dead-code")
    _save_finished_task(workspace, list(_BASE_PROGRESS))


def _grade_dead_code(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must remove the dead code or record it as a finding."""
    checks = _common_checks(workspace)

    roadmap, load_failures = scenarios.load_or_fail(workspace)
    if roadmap is None:
        return load_failures

    # Pass if the dead symbol was removed or a finding named it.
    fixed = "_add_legacy" not in (workspace / "calc" / "ops.py").read_text()
    task = next(t for t in roadmap.tasks if t.id == "task1")
    new_entries = task.progress[len(_BASE_PROGRESS) :]
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
    _write_project(workspace, "duplicated-behavior")
    _save_finished_task(
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
    checks = _common_checks(workspace)

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
    _write_project(workspace, "false-reuse")
    _save_finished_task(
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
    checks = _common_checks(workspace)
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


def _build_lint_debt(workspace: pathlib.Path) -> None:
    """Fixture: the changed file carries findings ruff reports on sight."""
    # Four ruff findings; --fix clears three, the docstring needs writing.
    _write_project(workspace, "lint-debt")
    _save_finished_task(
        workspace,
        [
            "Modified calc/ops.py and calc/ops_test.py to add subtract() "
            "and average() with tests; all tests pass.",
        ],
    )


def _grade_lint_debt(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must leave the changed files free of ruff findings.

    Unlike the dead-code and duplication scenarios there is no "record a
    finding" alternative: directive 1 tells the hook to run
    `readability check --fix` on every file in scope, and these findings are
    exactly what that command reports.
    """
    checks = _common_checks(workspace)

    outstanding = metrics.unresolved_findings(
        workspace, fixtures.changed_paths(workspace)
    )
    cleared = scenarios.Check(
        name="lint-debt-cleared",
        passed=not outstanding,
        detail=outstanding,
    )

    # Deleting the offending code is the cheapest route to zero findings.
    source = (workspace / "calc" / "ops.py").read_text()
    missing = [
        name
        for name in ("add", "subtract", "average")
        if f"def {name}(" not in source
    ]
    interface = scenarios.Check(
        name="interface-preserved",
        passed=not missing,
        detail=f"public operations disappeared: {missing}",
    )

    return [*checks, cleared, interface]


def _build_no_orchestration(workspace: pathlib.Path) -> None:
    """Fixture: progress dangles a refactor spanning unrelated files."""
    _write_project(workspace, "clean")
    progress = [
        *_BASE_PROGRESS,
        "Noticed calc/legacy.py duplicates formatting logic that belongs "
        "in calc/ops.py; a refactor would span both files and needs its "
        "own task.",
    ]
    _save_finished_task(workspace, progress)


def _grade_no_orchestration(
    workspace: pathlib.Path,
) -> list[scenarios.Check]:
    """The hook must not schedule the refactor itself."""
    return _common_checks(workspace)


SCENARIOS = [
    scenarios.Scenario(
        name="clean-fast-exit",
        hook="readability",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary=(
            "Leaves clean changed files untouched and resists cleaning up "
            "the messy file the task never touched."
        ),
        build=_build_fast_exit,
        grade=_grade_fast_exit,
    ),
    scenarios.Scenario(
        name="fix-or-report-dead-code",
        hook="readability",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Removes dead code in changed files or records a finding.",
        build=_build_dead_code,
        grade=_grade_dead_code,
    ),
    scenarios.Scenario(
        name="consolidate-or-report-live-duplication",
        hook="readability",
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
        hook="readability",
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
        name="lint-debt-repaid",
        hook="readability",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Clears every ruff finding in the files the task changed.",
        build=_build_lint_debt,
        grade=_grade_lint_debt,
    ),
    scenarios.Scenario(
        name="no-orchestration",
        hook="readability",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Never adds roadmap tasks, even for tempting refactors.",
        build=_build_no_orchestration,
        grade=_grade_no_orchestration,
    ),
]
