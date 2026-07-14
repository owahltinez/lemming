"""Scenario framework and shared grader helpers for all eval suites."""

import dataclasses
import pathlib
import typing

from .. import models
from . import fixtures


@dataclasses.dataclass(frozen=True)
class Check:
    """Result of a single graded assertion against a workspace.

    Attributes:
        name: Short identifier of the property being checked.
        passed: Whether the property held.
        detail: Human-readable context for a failure.
        advisory: True for checks that grade a semantic property through a
            proxy (e.g. a keyword match). An advisory failure means
            "inspect the workspace", not "defect": it is reported but does
            not fail the trial, so hard reds stay trustworthy.
    """

    name: str
    passed: bool
    detail: str = ""
    advisory: bool = False


@dataclasses.dataclass(frozen=True)
class Scenario:
    """A hermetic eval case for one prompt-driven component.

    Attributes:
        name: Unique scenario name within its suite.
        hook: Hook to run against the fixture (e.g. "roadmap").
        outcome: Terminal status of the finished task the hook reacts to.
        task_id: ID of the finished task inside the fixture roadmap.
        summary: One-line description of the behavior under eval.
        build: Seeds a workspace directory with the fixture repo and tasks
            file.
        grade: Inspects the workspace after the trial and returns checks.
    """

    name: str
    hook: str
    outcome: models.TaskStatus
    task_id: str
    summary: str
    build: typing.Callable[[pathlib.Path], None]
    grade: typing.Callable[[pathlib.Path], list[Check]]


def passed(checks: list[Check]) -> bool:
    """Returns True when every non-advisory check in a trial passed."""
    return all(check.passed for check in checks if not check.advisory)


def load_or_fail(
    workspace: pathlib.Path,
) -> tuple[models.Roadmap | None, list[Check]]:
    """Loads the roadmap, converting load errors into a failed check."""
    try:
        return fixtures.load_roadmap(workspace), []
    except Exception as exc:
        check = Check(name="roadmap-loads", passed=False, detail=str(exc))
        return None, [check]


def check_clean_source(workspace: pathlib.Path) -> Check:
    """Checks that the trial left the source tree completely untouched."""
    dirty = fixtures.dirty_paths(workspace)
    return Check(
        name="no-source-changes",
        passed=not dirty,
        detail=f"modified files: {dirty}" if dirty else "",
    )


def check_finalized(roadmap: models.Roadmap, task_id: str) -> Check:
    """Checks that the finished task ended up marked as completed."""
    task = next((t for t in roadmap.tasks if t.id == task_id), None)
    status = task.status if task else "missing"
    return Check(
        name="task-finalized",
        passed=task is not None and task.status == models.TaskStatus.COMPLETED,
        detail=f"{task_id} status: {status}",
    )
