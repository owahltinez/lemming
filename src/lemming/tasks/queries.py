import pathlib
import time

from .. import models, paths, persistence
from . import lifecycle


def get_project_data(tasks_file: pathlib.Path) -> models.ProjectData:
    """Consolidated logic to get project state (tasks, context, loop status).

    Args:
        tasks_file: Path to the tasks YAML file.

    Returns:
        A ProjectData containing the enriched project state.
    """
    with persistence.read_lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
    now = time.time()

    # Deduplicate tasks by ID and enrich metadata.
    seen_ids = set()
    unique_tasks = []
    loop_running = lifecycle.is_loop_running(tasks_file)

    for i, t in enumerate(data.tasks):
        if t.id in seen_ids:
            continue
        seen_ids.add(t.id)
        unique_tasks.append(t)

        t.index = i
        t.has_runner_log = paths.get_log_file(tasks_file, t.id).exists()

        # Detect active loop from task heartbeats if not already known.
        if not loop_running and lifecycle.is_task_active(t, now):
            loop_running = True

    # Unified sort: uncompleted (pending, in_progress) first, then completed (completed, failed).
    # Within each group:
    # - Uncompleted: in_progress tasks first, then by index (YAML order), then by created_at.
    # - Completed: newer tasks appear first (reverse chronological).
    def sort_key(t):
        is_done = t.status in (
            models.TaskStatus.COMPLETED,
            models.TaskStatus.FAILED,
            models.TaskStatus.CANCELLED,
        )
        is_in_progress = t.status == models.TaskStatus.IN_PROGRESS
        if not is_done:
            # For pending/in_progress, prioritize in_progress and then YAML order (index)
            return (
                0,
                0 if is_in_progress else 1,
                (t.index if t.index is not None else 0),
                (t.created_at or 0),
            )
        else:
            # For completed/failed, we want newest first
            return (
                1,
                -(t.completed_at or t.created_at or 0),
                -(t.index if t.index is not None else 0),
            )

    unique_tasks.sort(key=sort_key)

    return models.ProjectData(
        context=data.context,
        tasks=unique_tasks,
        config=data.config,
        cwd=str(paths.get_working_dir(tasks_file)),
        loop_running=loop_running,
    )


def get_pending_task(data: models.Roadmap) -> models.Task | None:
    """Finds the next task to be executed.

    Args:
        data: The Roadmap to search.

    Returns:
        The next Task to execute, or None if no tasks are pending or a task
        is already in progress.
    """
    now = time.time()

    # 1. Check if ANY task is currently active (running or running hooks).
    # If so, we must not start any other task (unless it's finalizing).
    for task in data.tasks:
        if lifecycle.is_task_active(task, now):
            return None

        # If it's NOT active, but it has a requested_status, it means it's a
        # crashed finalizing task. We MUST pick it up immediately.
        if task.requested_status:
            return task

    # 2. Sort uncompleted tasks (pending or stale in_progress) to pick the oldest one (FIFO).
    # Note: we exclude tasks that are currently finalizing (have requested_status).
    uncompleted = []
    for i, task in enumerate(data.tasks):
        if (
            task.status in (models.TaskStatus.PENDING, models.TaskStatus.IN_PROGRESS)
            and not task.requested_status
        ):
            # We need to preserve the original index for tie-breaking
            task.index = i
            uncompleted.append(task)

    if not uncompleted:
        return None

    def sort_key(t):
        return (
            (t.index if t.index is not None else 0),
            (t.created_at or 0),
        )

    uncompleted.sort(key=sort_key)
    return uncompleted[0]
