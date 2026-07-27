"""Task CRUD operations: add, update, delete, and goal updates."""

import os
import pathlib
import time

from .. import models, paths, persistence
from . import lifecycle, limits, queries

_DONE_STATUSES = (
    models.TaskStatus.COMPLETED,
    models.TaskStatus.FAILED,
    models.TaskStatus.CANCELLED,
    models.TaskStatus.SUPERSEDED,
)


def _insert_at_queue_index(
    tasks: list[models.Task],
    task: models.Task,
    index: int,
) -> list[models.Task]:
    """Insert a task at a displayed queue index, before completed history."""
    queue = [item for item in tasks if item.status not in _DONE_STATUSES]
    completed = [item for item in tasks if item.status in _DONE_STATUSES]
    active_count = sum(
        item.status == models.TaskStatus.IN_PROGRESS for item in queue
    )
    queue.sort(key=lambda item: item.status != models.TaskStatus.IN_PROGRESS)

    if index == -1:
        index = len(queue)
    if index < active_count:
        raise ValueError(
            f"Index {index} is before {active_count} in-progress task(s)"
        )
    if index > len(queue):
        raise ValueError(
            f"Index {index} is outside the queue (maximum {len(queue)})"
        )

    queue.insert(index, task)
    return queue + completed


def add_task(
    tasks_file: pathlib.Path,
    description: str,
    runner: str | None = None,
    index: int = -1,
    parent: str | None = None,
    parent_tasks_file: str | None = None,
) -> models.Task:
    """Adds a new task to the roadmap.

    Args:
        tasks_file: Path to the tasks YAML file.
        description: Description of the task.
        runner: Optional preferred runner for this task.
        index: Position to insert the task at (default: append).
        parent: Optional parent task ID.
        parent_tasks_file: Optional parent tasks file path.

    Returns:
        The newly created Task.
    """
    limits.validate_task_description(tasks_file, description)

    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)

        task_id = lifecycle.generate_task_id()
        existing_ids = {t.id for t in data.tasks}
        while (
            task_id in existing_ids
            or (
                paths.get_project_dir(tasks_file) / f"{task_id}-runner.log"
            ).exists()
        ):
            task_id = lifecycle.generate_task_id()

        # Detect if we are running inside an agent and set parent automatically
        if not parent:
            parent = os.environ.get("LEMMING_PARENT_TASK_ID")
            if not parent_tasks_file:
                parent_tasks_file = os.environ.get("LEMMING_PARENT_TASKS_FILE")

        new_task = models.Task(
            id=task_id,
            description=description,
            runner=runner,
            parent=parent,
            parent_tasks_file=parent_tasks_file,
        )

        data.tasks = _insert_at_queue_index(data.tasks, new_task, index)

        persistence.save_tasks(tasks_file, data)
    return new_task


def delete_tasks(
    tasks_file: pathlib.Path,
    task_id: str | None = None,
    all_tasks: bool = False,
    completed_only: bool = False,
    force: bool = False,
) -> int:
    """Deletes tasks from the roadmap.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: Optional ID of a specific task to delete.
        all_tasks: If True, deletes all tasks and clears the goal.
        completed_only: If True, deletes terminal task history.
        force: Allow deleting an individual task with execution history.

    Returns:
        The number of tasks deleted.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        initial_count = len(data.tasks)

        if all_tasks:
            for t in data.tasks:
                lifecycle.reset_task_logs(tasks_file, t.id)
            data.tasks = []
            data.goal = ""
        elif completed_only:
            completed_tasks = [
                t
                for t in data.tasks
                if t.status
                in (
                    models.TaskStatus.COMPLETED,
                    models.TaskStatus.FAILED,
                    models.TaskStatus.CANCELLED,
                    models.TaskStatus.SUPERSEDED,
                )
            ]
            for t in completed_tasks:
                lifecycle.reset_task_logs(tasks_file, t.id)
            data.tasks = [
                t
                for t in data.tasks
                if t.status
                not in (
                    models.TaskStatus.COMPLETED,
                    models.TaskStatus.FAILED,
                    models.TaskStatus.CANCELLED,
                    models.TaskStatus.SUPERSEDED,
                )
            ]
        elif task_id:
            try:
                target = queries.resolve_task(data.tasks, task_id)
            except models.TaskNotFoundError:
                target = None
            if target:
                log_exists = (
                    paths.get_project_dir(tasks_file)
                    / f"{target.id}-runner.log"
                ).exists()
                if not force and (
                    target.status == models.TaskStatus.IN_PROGRESS
                    or target.attempts > 0
                    or target.started_at is not None
                    or log_exists
                ):
                    raise ValueError(
                        f"Task {target.id} has execution history. "
                        "Supersede it to preserve lineage, or use --force "
                        "to remove it explicitly."
                    )
                data.tasks = [t for t in data.tasks if t.id != target.id]

        persistence.save_tasks(tasks_file, data)
        return initial_count - len(data.tasks)


def supersede_task(
    tasks_file: pathlib.Path,
    task_id: str,
    reason: str,
) -> models.Task:
    """Retire a task while preserving its execution history and lineage.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: Full task ID or an unambiguous prefix.
        reason: Human-readable reason the task was replaced.

    Returns:
        The superseded task.

    Raises:
        ValueError: If the task is missing, already finished, or the reason is
            empty.
    """
    reason = reason.strip()
    if not reason:
        raise ValueError("Supersede reason cannot be empty")

    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = queries.resolve_task(data.tasks, task_id)
        if target.status in _DONE_STATUSES:
            raise ValueError(
                f"Cannot supersede task {target.id} with status {target.status}"
            )

        if target.status == models.TaskStatus.IN_PROGRESS:
            lifecycle.update_run_time(target)
        target.status = models.TaskStatus.SUPERSEDED
        target.superseded_at = time.time()
        target.superseded_reason = reason
        target.pid = None
        target.last_heartbeat = None
        target.requested_status = None
        target.active_execution_component = None
        target.active_execution_started_at = None

        persistence.save_tasks(tasks_file, data)
        return target


def update_task(
    tasks_file: pathlib.Path,
    task_id: str,
    description: str | None = None,
    runner: str | None = None,
    index: int | None = None,
    status: str | None = None,
    require_progress: bool = False,
    parent: str | None = None,
    parent_tasks_file: str | None = None,
    force: bool = False,
) -> models.Task:
    """Updates an existing task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task to update.
        description: New description.
        runner: New preferred runner.
        index: New position in the task list.
        status: New status.
        require_progress: If True, raises ValueError if the task has no
            progress.
        parent: New parent task ID.
        parent_tasks_file: New parent tasks file path.
        force: If True, force status transition even if task is in progress.

    Returns:
        The updated Task.

    Raises:
        ValueError: If the task is not found, or if validation fails.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)

        target = queries.resolve_task(data.tasks, task_id)
        task_idx = data.tasks.index(target)

        if target.status == models.TaskStatus.COMPLETED and description:
            raise ValueError("Cannot edit description of a completed task")

        if description is not None:
            limits.validate_task_description(tasks_file, description)

        if require_progress and not target.progress:
            raise ValueError(
                f"Task {target.id} has no recorded progress. "
                "Record at least one progress entry before completing "
                "or failing."
            )

        if description is not None:
            target.description = description
        if runner is not None:
            target.runner = runner
        if parent is not None:
            if parent == "":
                target.parent = None
            else:
                target.parent = parent
        if parent_tasks_file is not None:
            if parent_tasks_file == "":
                target.parent_tasks_file = None
            else:
                target.parent_tasks_file = parent_tasks_file

        if status and status != target.status:
            # If the task is currently in progress, we don't transition to
            # completed/failed immediately. We set requested_status so the
            # orchestrator can run hooks before final completion.
            if (
                not force
                and target.status == models.TaskStatus.IN_PROGRESS
                and status
                in (
                    models.TaskStatus.COMPLETED,
                    models.TaskStatus.FAILED,
                    models.TaskStatus.CANCELLED,
                    models.TaskStatus.SUPERSEDED,
                )
            ):
                lifecycle.update_run_time(target)
                target.requested_status = models.TaskStatus(status)
                target.last_started_at = (
                    time.time()
                )  # Track hook execution time
            else:
                if target.status == models.TaskStatus.IN_PROGRESS:
                    lifecycle.update_run_time(target)

                target.status = models.TaskStatus(status)
                if status in (
                    models.TaskStatus.COMPLETED,
                    models.TaskStatus.FAILED,
                    models.TaskStatus.CANCELLED,
                    models.TaskStatus.SUPERSEDED,
                ):
                    target.completed_at = time.time()
                    target.pid = None
                    target.last_heartbeat = None
                    target.requested_status = None
                    target.active_execution_component = None
                    target.active_execution_started_at = None
                elif status == models.TaskStatus.PENDING:
                    target.completed_at = None
                    target.superseded_at = None
                    target.superseded_reason = None
                    target.attempts = 0
                    target.requested_status = None
                    target.active_execution_component = None
                    target.active_execution_started_at = None
                elif target.completed_at is not None:
                    target.completed_at = None

        if index is not None:
            if target.status in _DONE_STATUSES:
                raise ValueError("Cannot move a finished task")
            if target.status == models.TaskStatus.IN_PROGRESS:
                raise ValueError("Cannot move an in-progress task")
            data.tasks.pop(task_idx)
            data.tasks = _insert_at_queue_index(data.tasks, target, index)

        persistence.save_tasks(tasks_file, data)
    return target


def update_goal(tasks_file: pathlib.Path, goal: str) -> None:
    """Updates the project's long-term goal.

    Args:
        tasks_file: Path to the tasks YAML file.
        goal: The new long-term goal string.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        data.goal = goal
        persistence.save_tasks(tasks_file, data)
