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

# The state of the module before the finished task ran: subtract() and its
# test are what the task added, so the fixture's second commit is a diff that
# matches the task description instead of prose the hook has to take on
# faith.
_BASELINE_OPS = '''"""Arithmetic operations for the calculator CLI."""


def add(a: float, b: float) -> float:
    """Returns the sum of two numbers."""
    return a + b
'''

_BASELINE_OPS_TEST = """import unittest

from calc import ops


class TestOps(unittest.TestCase):
    def test_add(self):
        self.assertEqual(ops.add(2, 3), 5)
"""

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

_DUPLICATED_BEHAVIOR_OPS = (
    '"""Arithmetic operations for the calculator CLI."""\n\n'
    '''import math


def add(a: float, b: float) -> float:
    """Returns the sum of two numbers."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Returns the difference of two numbers."""
    return a - b


def add_for_receipt(a: float, b: float) -> float:
    """Returns a validated, receipt-ready sum."""
    total = a + b
    if not math.isfinite(total):
        raise ValueError("Receipt totals must be finite.")
    return round(total, 2)


def subtract_for_receipt(a: float, b: float) -> float:
    """Returns a validated, receipt-ready difference."""
    total = a - b
    if not math.isfinite(total):
        raise ValueError("Receipt totals must be finite.")
    return round(total, 2)
'''
)

_OPS_TEST = """import unittest

from calc import ops


class TestOps(unittest.TestCase):
    def test_add(self):
        self.assertEqual(ops.add(2, 3), 5)

    def test_subtract(self):
        self.assertEqual(ops.subtract(5, 3), 2)
"""

_DUPLICATED_BEHAVIOR_TEST = """import unittest

from calc import ops


class TestOps(unittest.TestCase):
    def test_add(self):
        self.assertEqual(ops.add(2, 3), 5)

    def test_subtract(self):
        self.assertEqual(ops.subtract(5, 3), 2)

    def test_add_for_receipt(self):
        self.assertEqual(ops.add_for_receipt(2.126, 1), 3.13)

    def test_subtract_for_receipt(self):
        self.assertEqual(ops.subtract_for_receipt(5.126, 2), 3.13)

    def test_receipt_totals_must_be_finite(self):
        with self.assertRaises(ValueError):
            ops.add_for_receipt(float("inf"), 1)
        with self.assertRaises(ValueError):
            ops.subtract_for_receipt(float("inf"), 1)
"""

# Two clamps with the same shape and unrelated meanings. The bounds differ
# and the comment says outright that they are independent, so folding them
# into one helper is the "false reuse" the prompt tells the hook to resist.
_FALSE_REUSE_LIMITS = '''"""Bounds used by the calculator CLI."""

# clamp_percentage and clamp_retries look alike by coincidence. One bounds a
# number for display, the other bounds a retry budget in the backoff loop.
# They change for different reasons and share no rule, so they stay as two
# independent definitions.

MAX_PERCENT = 100.0
MAX_RETRIES = 5


def clamp_percentage(value: float) -> float:
    """Returns a percentage clamped to the 0-100 display range."""
    if value < 0.0:
        return 0.0
    if value > MAX_PERCENT:
        return MAX_PERCENT
    return value


def clamp_retries(attempts: int) -> int:
    """Returns a retry count clamped to the backoff loop's budget."""
    if attempts < 0:
        return 0
    if attempts > MAX_RETRIES:
        return MAX_RETRIES
    return attempts
'''

_FALSE_REUSE_LIMITS_TEST = """import unittest

from calc import limits


class TestLimits(unittest.TestCase):
    def test_percentage_is_clamped_to_the_display_range(self):
        self.assertEqual(limits.clamp_percentage(-4.0), 0.0)
        self.assertEqual(limits.clamp_percentage(140.0), 100.0)
        self.assertEqual(limits.clamp_percentage(42.5), 42.5)

    def test_retries_are_clamped_to_the_backoff_budget(self):
        self.assertEqual(limits.clamp_retries(-1), 0)
        self.assertEqual(limits.clamp_retries(9), 5)
        self.assertEqual(limits.clamp_retries(3), 3)
"""

# The same assertions, copied in only at grading time. The visible suite is
# in scope and so fair game for the agent to edit; this copy is what pins
# the behavior the fixture actually promised.
_FALSE_REUSE_HIDDEN_TEST = "calc/limits_hidden_test.py"

# A changed file carrying four findings lemming's ruff configuration
# reports: an unused import, an unsorted import block, a missing docstring,
# and an over-long line. `readability check --fix` clears three; the
# docstring needs the hook to write one.
_LINT_DEBT_OPS = (
    '''"""Arithmetic operations for the calculator CLI."""

import sys
import math


def add(a: float, b: float) -> float:
    """Returns the sum of two numbers."""
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def average(values: list[float]) -> float:
    """Returns the mean of the given values."""
'''
    # Split across two source lines so this module keeps its own 80-column
    # limit while the fixture line genuinely breaks it.
    "    return round(math.fsum([float(value) for value in values])"
    " / max(len(values), 1), 4)\n"
)

_LINT_DEBT_TEST = """import unittest

from calc import ops


class TestOps(unittest.TestCase):
    def test_add(self):
        self.assertEqual(ops.add(2, 3), 5)

    def test_subtract(self):
        self.assertEqual(ops.subtract(5, 3), 2)

    def test_average(self):
        self.assertEqual(ops.average([1, 2, 3]), 2.0)

    def test_average_of_nothing(self):
        self.assertEqual(ops.average([]), 0.0)
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


def _init_project(workspace: pathlib.Path, changes: dict[str, str]) -> None:
    """Seeds the calculator fixture with a finished task's changes on top.

    The changes land in a second commit so the trial starts with the
    finished task's work as a reviewable diff; everything else is baseline
    the hook is expected to leave alone.
    """
    fixtures.init_repo(
        workspace,
        {
            "calc/__init__.py": "",
            "calc/ops.py": _BASELINE_OPS,
            "calc/ops_test.py": _BASELINE_OPS_TEST,
            "calc/legacy.py": _MESSY_LEGACY,
            "README.md": "# Calculator CLI\n",
        },
        changes=changes,
    )


def _write_project(
    workspace: pathlib.Path,
    ops_source: str,
    test_source: str = _OPS_TEST,
) -> None:
    """Seeds the fixture project with the given ops module as the change."""
    _init_project(
        workspace,
        {"calc/ops.py": ops_source, "calc/ops_test.py": test_source},
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


def _build_duplicated_behavior(workspace: pathlib.Path) -> None:
    """Fixture: two live functions duplicate receipt preparation."""
    _write_project(
        workspace,
        _DUPLICATED_BEHAVIOR_OPS,
        _DUPLICATED_BEHAVIOR_TEST,
    )
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
    _init_project(
        workspace,
        {
            "calc/limits.py": _FALSE_REUSE_LIMITS,
            "calc/limits_test.py": _FALSE_REUSE_LIMITS_TEST,
        },
    )
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

    # Doing nothing passes, and so does any edit that leaves two clamps
    # which neither share a factored-out helper nor call each other. A new
    # module-level name means a helper appeared; a call between the two
    # means one was rewritten in terms of the other.
    clamps = ["clamp_percentage", "clamp_retries"]
    functions = metrics.top_level_functions(source)
    calls = set().union(*(metrics.called_names(source, c) for c in clamps))
    coupled = bool(calls & set(clamps))
    independent = scenarios.Check(
        name="clamps-stayed-independent",
        passed=functions == clamps and not coupled,
        detail=f"limits.py defines {functions}, coupled={coupled}",
    )

    # The visible suite is in scope, so an agent that changed a clamp and
    # moved its test would still look green; this copy is not.
    hidden = metrics.run_hidden_tests(
        workspace, {_FALSE_REUSE_HIDDEN_TEST: _FALSE_REUSE_LIMITS_TEST}
    )
    preserved = scenarios.Check(
        name="clamp-behavior-preserved",
        passed=hidden.passed,
        detail=hidden.detail,
    )

    return [*checks, independent, preserved]


def _build_lint_debt(workspace: pathlib.Path) -> None:
    """Fixture: the changed file carries findings ruff reports on sight."""
    _write_project(workspace, _LINT_DEBT_OPS, _LINT_DEBT_TEST)
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
