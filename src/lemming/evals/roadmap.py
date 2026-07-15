"""Eval scenarios for the roadmap hook prompt.

Each scenario seeds a tiny calculator project mid-flight, lets the roadmap
hook react to a task that just finished, and grades the resulting roadmap
with mechanical checks derived from the prompt's directives: repair failed
tasks with a new approach, prune redundant work, extend an unmet goal, add
follow-up tasks, never touch source files, and fast-exit when healthy.
"""

import pathlib

from .. import models
from . import fixtures, scenarios

_RETRIES = 3

_GOAL = (
    "Build a small calculator CLI in calc/ with add, subtract, and "
    "multiply commands, each covered by unit tests."
)

_ADD_ONLY_OPS = '''"""Arithmetic operations for the calculator CLI."""


def add(a, b):
    """Returns the sum of two numbers."""
    return a + b
'''

_ADD_ONLY_TEST = """import unittest

from calc import ops


class TestOps(unittest.TestCase):
    def test_add(self):
        self.assertEqual(ops.add(2, 3), 5)
"""

_FULL_OPS = '''"""Arithmetic operations for the calculator CLI."""


def add(a, b):
    """Returns the sum of two numbers."""
    return a + b


def subtract(a, b):
    """Returns the difference of two numbers."""
    return a - b


def multiply(a, b):
    """Returns the product of two numbers."""
    return a * b
'''

_BUGGY_OPS = '''"""Arithmetic operations for the calculator CLI."""


def add(a, b):
    """Returns the sum of two numbers."""
    return a + b


def subtract(a, b):
    """Returns the difference of two numbers."""
    return a + b


def multiply(a, b):
    """Returns the product of two numbers."""
    return a * b
'''

_REPAIR_DESCRIPTION = (
    "Implement the subtract and multiply commands in calc/cli.py with "
    "unit tests."
)

_REDUNDANT_DESCRIPTION = (
    "Implement the multiply command in calc/ops.py with unit tests."
)


def _config() -> models.RoadmapConfig:
    """Returns a deterministic roadmap config for fixtures."""
    return models.RoadmapConfig(
        retries=_RETRIES, runner="claude", time_limit=15
    )


def _write_project(workspace: pathlib.Path, ops_source: str) -> None:
    """Seeds the fixture calculator project with the given ops module."""
    fixtures.init_repo(
        workspace,
        {
            "calc/__init__.py": "",
            "calc/ops.py": ops_source,
            "calc/ops_test.py": _ADD_ONLY_TEST,
            "README.md": "# Calculator CLI\n",
        },
    )


def _build_repair(workspace: pathlib.Path) -> None:
    """Fixture: task1 failed identically three times and is out of retries."""
    _write_project(workspace, _ADD_ONLY_OPS)
    failure = (
        "Attempt failed: created calc/cli.py but tests error with "
        "ImportError: cannot import name 'dispatch' from calc.ops."
    )
    fixtures.save_roadmap(
        workspace,
        models.Roadmap(
            goal=_GOAL,
            config=_config(),
            tasks=[
                models.Task(
                    id="task1",
                    description=_REPAIR_DESCRIPTION,
                    attempts=_RETRIES,
                    progress=[failure, failure, failure],
                ),
                models.Task(
                    id="task2",
                    description=(
                        "Document CLI usage examples in README.md once all "
                        "commands exist."
                    ),
                ),
            ],
        ),
    )


def _grade_repair(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must change the approach, not stall or naively reset."""
    roadmap, checks = scenarios.load_or_fail(workspace)
    if roadmap is None:
        return checks

    # An unchanged description means the same failure will repeat (naive
    # reset) or the project aborts outright (still out of retries).
    task = next((t for t in roadmap.tasks if t.id == "task1"), None)
    if task is None:
        repaired = scenarios.Check(name="repaired", passed=True)
    elif task.description == _REPAIR_DESCRIPTION:
        reason = (
            "naive reset: attempts cleared but approach unchanged"
            if task.attempts < _RETRIES
            else "no intervention: task still failed at max attempts"
        )
        repaired = scenarios.Check(name="repaired", passed=False, detail=reason)
    else:
        repaired = scenarios.Check(
            name="repaired",
            passed=task.attempts < _RETRIES,
            detail="description rewritten but attempts not reset"
            if task.attempts >= _RETRIES
            else "",
        )

    return [repaired, scenarios.check_clean_source(workspace)]


def _build_fast_exit(workspace: pathlib.Path) -> None:
    """Fixture: a healthy roadmap where the finished task went perfectly."""
    _write_project(workspace, _ADD_ONLY_OPS)
    fixtures.save_roadmap(
        workspace,
        models.Roadmap(
            goal=_GOAL,
            config=_config(),
            tasks=[
                models.Task(
                    id="task1",
                    description=(
                        "Implement the add command in calc/ops.py with unit "
                        "tests."
                    ),
                    attempts=1,
                    requested_status=models.TaskStatus.COMPLETED,
                    progress=[
                        "Implemented add() in calc/ops.py and covered it in "
                        "calc/ops_test.py; all tests pass."
                    ],
                ),
                models.Task(
                    id="task2",
                    description=(
                        "Implement the subtract command in calc/ops.py with "
                        "unit tests."
                    ),
                ),
                models.Task(
                    id="task3",
                    description=_REDUNDANT_DESCRIPTION,
                ),
            ],
        ),
    )


def _grade_fast_exit(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must leave an accurate roadmap alone."""
    roadmap, checks = scenarios.load_or_fail(workspace)
    if roadmap is None:
        return checks

    # Any structural change to a healthy roadmap is churn: same three
    # tasks, same descriptions, and the two pending ones still pending.
    expected = {
        ("task2", models.TaskStatus.PENDING),
        ("task3", models.TaskStatus.PENDING),
    }
    actual = {
        (t.id, t.status) for t in roadmap.tasks if t.id in ("task2", "task3")
    }
    untouched = scenarios.Check(
        name="roadmap-untouched",
        passed=len(roadmap.tasks) == 3 and actual == expected,
        detail=f"tasks now: {[(t.id, str(t.status)) for t in roadmap.tasks]}",
    )

    return [
        scenarios.check_finalized(roadmap, "task1"),
        untouched,
        scenarios.check_clean_source(workspace),
    ]


def _build_prune(workspace: pathlib.Path) -> None:
    """Fixture: the finished task also did task2's work, making it moot."""
    _write_project(workspace, _FULL_OPS)
    fixtures.save_roadmap(
        workspace,
        models.Roadmap(
            goal=_GOAL,
            config=_config(),
            tasks=[
                models.Task(
                    id="task1",
                    description=(
                        "Implement the subtract command in calc/ops.py with "
                        "unit tests."
                    ),
                    attempts=1,
                    requested_status=models.TaskStatus.COMPLETED,
                    progress=[
                        "Implemented subtract() in calc/ops.py with tests. "
                        "While refactoring the dispatch table I also "
                        "implemented multiply() with tests, so task2 "
                        "(multiply) is now redundant and should be removed."
                    ],
                ),
                models.Task(id="task2", description=_REDUNDANT_DESCRIPTION),
            ],
        ),
    )


def _grade_prune(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must remove or rewrite the now-redundant pending task."""
    roadmap, checks = scenarios.load_or_fail(workspace)
    if roadmap is None:
        return checks

    stale = [
        t.id
        for t in roadmap.tasks
        if t.status == models.TaskStatus.PENDING
        and t.description == _REDUNDANT_DESCRIPTION
    ]
    pruned = scenarios.Check(
        name="redundant-task-pruned",
        passed=not stale,
        detail=f"still pending unchanged: {stale}" if stale else "",
    )

    return [
        scenarios.check_finalized(roadmap, "task1"),
        pruned,
        scenarios.check_clean_source(workspace),
    ]


def _build_extend(workspace: pathlib.Path) -> None:
    """Fixture: all tasks done but the goal still lacks multiply."""
    _write_project(workspace, _ADD_ONLY_OPS)
    fixtures.save_roadmap(
        workspace,
        models.Roadmap(
            goal=_GOAL,
            config=_config(),
            tasks=[
                models.Task(
                    id="task0",
                    description=(
                        "Implement the add command in calc/ops.py with unit "
                        "tests."
                    ),
                    status=models.TaskStatus.COMPLETED,
                    attempts=1,
                    progress=["Implemented add() with tests."],
                ),
                models.Task(
                    id="task1",
                    description=(
                        "Implement the subtract command in calc/ops.py with "
                        "unit tests."
                    ),
                    attempts=1,
                    requested_status=models.TaskStatus.COMPLETED,
                    progress=[
                        "Implemented subtract() with tests. Note: the goal "
                        "also requires a multiply command but no roadmap "
                        "task covers it yet."
                    ],
                ),
            ],
        ),
    )


def _grade_extend(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must add concrete work to close the stated gap."""
    roadmap, checks = scenarios.load_or_fail(workspace)
    if roadmap is None:
        return checks

    new_pending = [
        t
        for t in roadmap.tasks
        if t.id not in ("task0", "task1")
        and t.status == models.TaskStatus.PENDING
    ]
    extended = scenarios.Check(
        name="roadmap-extended",
        passed=bool(new_pending),
        detail="no new pending tasks were added" if not new_pending else "",
    )
    # Keyword proxy for "the added task actually covers the gap": a red
    # here means inspect the workspace, not necessarily a prompt defect.
    covered = scenarios.Check(
        name="gap-covered",
        passed=any("multiply" in t.description.lower() for t in new_pending),
        detail=f"new tasks: {[t.description for t in new_pending]}",
        advisory=True,
    )

    return [
        scenarios.check_finalized(roadmap, "task1"),
        extended,
        covered,
        scenarios.check_clean_source(workspace),
    ]


def _build_follow_up(workspace: pathlib.Path) -> None:
    """Fixture: the finished task reported an out-of-scope bug in source."""
    _write_project(workspace, _BUGGY_OPS)
    fixtures.save_roadmap(
        workspace,
        models.Roadmap(
            goal=_GOAL,
            config=_config(),
            tasks=[
                models.Task(
                    id="task1",
                    description=_REDUNDANT_DESCRIPTION,
                    attempts=1,
                    requested_status=models.TaskStatus.COMPLETED,
                    progress=[
                        "Implemented multiply() in calc/ops.py with tests.",
                        "Found a bug while reading calc/ops.py: "
                        "subtract(a, b) returns a + b instead of a - b. Out "
                        "of scope for this task; needs a follow-up fix.",
                    ],
                ),
            ],
        ),
    )


def _grade_follow_up(workspace: pathlib.Path) -> list[scenarios.Check]:
    """The hook must schedule the reported bug, never fix code itself."""
    roadmap, checks = scenarios.load_or_fail(workspace)
    if roadmap is None:
        return checks

    new_pending = [
        t
        for t in roadmap.tasks
        if t.id != "task1" and t.status == models.TaskStatus.PENDING
    ]
    scheduled = scenarios.Check(
        name="follow-up-added",
        passed=bool(new_pending),
        detail="no follow-up task was added for the reported bug"
        if not new_pending
        else "",
    )
    # Keyword proxy for "the follow-up targets the reported subtract bug".
    targeted = scenarios.Check(
        name="follow-up-targets-bug",
        passed=any("subtract" in t.description.lower() for t in new_pending),
        detail=f"new tasks: {[t.description for t in new_pending]}",
        advisory=True,
    )

    return [
        scenarios.check_finalized(roadmap, "task1"),
        scheduled,
        targeted,
        scenarios.check_clean_source(workspace),
    ]


SCENARIOS = [
    scenarios.Scenario(
        name="repair-exhausted-failure",
        hook="roadmap",
        outcome=models.TaskStatus.FAILED,
        task_id="task1",
        summary="Rewrites or replaces a task that failed at max attempts.",
        build=_build_repair,
        grade=_grade_repair,
    ),
    scenarios.Scenario(
        name="fast-exit-healthy",
        hook="roadmap",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Leaves an accurate, healthy roadmap untouched.",
        build=_build_fast_exit,
        grade=_grade_fast_exit,
    ),
    scenarios.Scenario(
        name="prune-redundant-task",
        hook="roadmap",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Removes a pending task made redundant by finished work.",
        build=_build_prune,
        grade=_grade_prune,
    ),
    scenarios.Scenario(
        name="extend-goal-unmet",
        hook="roadmap",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Adds concrete tasks when the goal has an uncovered gap.",
        build=_build_extend,
        grade=_grade_extend,
    ),
    scenarios.Scenario(
        name="follow-up-without-code-changes",
        hook="roadmap",
        outcome=models.TaskStatus.COMPLETED,
        task_id="task1",
        summary="Schedules reported bugs as tasks without touching source.",
        build=_build_follow_up,
        grade=_grade_follow_up,
    ),
]
