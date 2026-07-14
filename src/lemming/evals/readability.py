"""Eval scenarios for the readability hook prompt.

Each scenario seeds the calculator project just after a task finished, lets
the readability hook review the changed files, and grades the aftermath
against the prompt's contract: fast-exit on clean code, act on quality
drift in the changed files (fix it or record a finding), never touch files
outside the finished task's scope, keep the tests green, and never add
roadmap tasks.
"""

import pathlib
import subprocess
import sys

from .. import models
from . import fixtures, scenarios

_GOAL = (
    "Build a small calculator CLI in calc/ with add, subtract, and "
    "multiply commands, each covered by unit tests."
)

# Fully annotated so it is clean by the Google style guide the hook
# enforces: an eval run showed the hook (correctly) adds missing type
# annotations, so an unannotated fixture cannot demand a fast exit.
_CLEAN_OPS = '''"""Arithmetic operations for the calculator CLI."""


def add(a: float, b: float) -> float:
    """Returns the sum of two numbers."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Returns the difference of two numbers."""
    return a - b
'''

# The same module with an obviously dead duplicate: cleanly formatted so
# automated tools stay silent and only a genuine review can catch it.
_DEAD_CODE_OPS = '''"""Arithmetic operations for the calculator CLI."""


def add(a: float, b: float) -> float:
    """Returns the sum of two numbers."""
    return a + b


def _add_legacy(a: float, b: float) -> float:
    """Deprecated duplicate of add kept from an earlier refactor."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Returns the difference of two numbers."""
    return a - b
'''

_OPS_TEST = """import unittest

from calc import ops


class TestOps(unittest.TestCase):
    def test_add(self):
        self.assertEqual(ops.add(2, 3), 5)

    def test_subtract(self):
        self.assertEqual(ops.subtract(5, 3), 2)
"""

# A messy module the finished task did NOT touch: tempting to clean up,
# but strictly out of scope for the hook.
_MESSY_LEGACY = '''"""Legacy report formatting kept for compatibility."""


def format_report(values, kind, upper, prefix):
    """Formats values into a report string."""
    out = ""
    for v in values:
        if kind == "int":
            if upper:
                if prefix:
                    out = out + prefix + str(int(v)).upper() + "\\n"
                else:
                    out = out + str(int(v)).upper() + "\\n"
            else:
                out = out + str(int(v)) + "\\n"
        else:
            if upper:
                out = out + str(v).upper() + "\\n"
            else:
                out = out + str(v) + "\\n"
    return out
'''

_FINISHED_DESCRIPTION = (
    "Implement the subtract command in calc/ops.py with unit tests."
)

_BASE_PROGRESS = [
    "Modified calc/ops.py and calc/ops_test.py to add subtract() with "
    "tests; all tests pass. No other files were touched."
]


def _write_project(workspace: pathlib.Path, ops_source: str) -> None:
    """Seeds the fixture project with the given ops module."""
    fixtures.init_repo(
        workspace,
        {
            "calc/__init__.py": "",
            "calc/ops.py": ops_source,
            "calc/ops_test.py": _OPS_TEST,
            "calc/legacy.py": _MESSY_LEGACY,
            "README.md": "# Calculator CLI\n",
        },
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
                hooks=["readability"],
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
    """Checks that files outside the finished task were left alone."""
    out_of_scope = [
        path
        for path in fixtures.dirty_paths(workspace)
        if path not in ("calc/ops.py", "calc/ops_test.py")
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
    _write_project(workspace, _CLEAN_OPS)
    _save_finished_task(workspace, list(_BASE_PROGRESS))


def _grade_fast_exit(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must leave clean changed files untouched."""
    return [
        *_common_checks(workspace),
        scenarios.check_clean_source(workspace),
    ]


def _build_dead_code(workspace: pathlib.Path) -> None:
    """Fixture: the changed file contains a dead duplicate function."""
    _write_project(workspace, _DEAD_CODE_OPS)
    _save_finished_task(workspace, list(_BASE_PROGRESS))


def _grade_dead_code(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must remove the dead code or record it as a finding."""
    checks = _common_checks(workspace)

    roadmap, load_failures = scenarios.load_or_fail(workspace)
    if roadmap is None:
        return load_failures

    # Either the drift was fixed in place or a finding naming the dead
    # symbol was recorded; silently ignoring it is the failure the prompt
    # change targets. Status noise like "checks passed" does not count —
    # a genuine finding about a function necessarily names it.
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


def _build_scope_limit(workspace: pathlib.Path) -> None:
    """Fixture: an untouched legacy file is messy but out of scope."""
    _write_project(workspace, _CLEAN_OPS)
    _save_finished_task(workspace, list(_BASE_PROGRESS))


def _grade_scope_limit(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must resist cleaning up files the task never touched."""
    return _common_checks(workspace)


def _build_no_orchestration(workspace: pathlib.Path) -> None:
    """Fixture: progress dangles a refactor spanning unrelated files."""
    _write_project(workspace, _CLEAN_OPS)
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
        summary="Leaves clean, idiomatic changed files untouched.",
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
        name="scope-limited-to-changed-files",
        hook="readability",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Resists cleaning up messy files outside the task's scope.",
        build=_build_scope_limit,
        grade=_grade_scope_limit,
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
