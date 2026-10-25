"""Eval scenarios for the readability hook prompt.

Each scenario seeds the calculator project just after a task finished, lets
the readability hook review the changed files, and grades the aftermath
against the prompt's contract: fast-exit on clean code, clear lint findings
in the changed files, never touch files outside the finished task's scope,
keep the tests green, and never add roadmap tasks.

Scenarios come in action/restraint pairs on purpose. A comparison of two
agent CLIs showed one arm consistently under-acting and the other
consistently over-acting, so a suite made only of "did the agent act"
cases would hand the win to whichever arm edits more, and a suite made
only of "did the agent hold back" cases would do the reverse. Every
scenario added here should have a counterpart pulling the other way:
lint-debt-repaid against clean-fast-exit.

The calculator harness here is shared with the simplify suite, which owns
the shape scenarios: dead code, duplication, and false reuse.
"""

import pathlib
import shutil
import subprocess
import sys

from .. import models
from . import container, fixtures, metrics, scenarios

_GOAL = (
    "Build a small calculator CLI in calc/ with add, subtract, and "
    "multiply commands, each covered by unit tests."
)

_FINISHED_DESCRIPTION = (
    "Implement the subtract command in calc/ops.py with unit tests."
)

BASE_PROGRESS = [
    "Modified calc/ops.py and calc/ops_test.py to add subtract() with "
    "tests; all tests pass. No other files were touched."
]


def write_project(workspace: pathlib.Path, change: str) -> None:
    """Seeds the calculator fixture with a finished task's changes on top.

    The changes land in a second commit so the trial starts with the
    finished task's work as a reviewable diff; everything else is baseline
    the hook is expected to leave alone.

    The base project also ships a messy calc/legacy.py that no scenario's
    task ever touches: tempting to clean up, and strictly out of scope.

    Args:
        workspace: Directory to seed.
        change: Fixture project holding the finished task's version of the
            files it touched, e.g. "readability/lint-debt".
    """
    fixtures.init_repo(
        workspace,
        fixtures.load_project("readability/base"),
        changes=fixtures.load_project(change),
    )


def save_finished_task(workspace: pathlib.Path, progress: list[str]) -> None:
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
    if shutil.which("docker"):
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "-v",
            f"{workspace}:{container.WORKSPACE_MOUNT}:ro",
            "-w",
            container.WORKSPACE_MOUNT,
            container.DEFAULT_IMAGE,
            "python3",
            "-m",
            "unittest",
            "discover",
            "-p",
            "*_test.py",
        ]
    else:
        cmd = [sys.executable, "-m", "unittest", "discover", "-p", "*_test.py"]
    result = subprocess.run(
        cmd,
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


def common_checks(workspace: pathlib.Path) -> list[scenarios.Check]:
    """Grades the contract every calculator scenario shares."""
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
    write_project(workspace, "readability/clean")
    save_finished_task(workspace, list(BASE_PROGRESS))


def _grade_fast_exit(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must leave clean changed files untouched."""
    return [
        *common_checks(workspace),
        scenarios.check_clean_source(workspace),
    ]


def _build_lint_debt(workspace: pathlib.Path) -> None:
    """Fixture: the changed file carries findings ruff reports on sight."""
    # Four ruff findings; --fix clears three, the docstring needs writing.
    write_project(workspace, "readability/lint-debt")
    save_finished_task(
        workspace,
        [
            "Modified calc/ops.py and calc/ops_test.py to add subtract() "
            "and average() with tests; all tests pass.",
        ],
    )


def _grade_lint_debt(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must leave the changed files free of ruff findings.

    Unlike the simplify suite's dead-code and duplication scenarios there
    is no "record a finding" alternative: directive 1 tells the hook to run
    `readability check --fix` on every file in scope, and these findings are
    exactly what that command reports.
    """
    checks = common_checks(workspace)

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
        name="lint-debt-repaid",
        hook="readability",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Clears every ruff finding in the files the task changed.",
        build=_build_lint_debt,
        grade=_grade_lint_debt,
    ),
]
