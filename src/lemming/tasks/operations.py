import os
import pathlib
import time

from .. import models, persistence
from . import lifecycle


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
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)

        task_id = lifecycle.generate_task_id()
        existing_ids = {t.id for t in data.tasks}
        while task_id in existing_ids:
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

        if index == -1:
            data.tasks.append(new_task)
        else:
            data.tasks.insert(index, new_task)

        persistence.save_tasks(tasks_file, data)
    return new_task


def delete_tasks(
    tasks_file: pathlib.Path,
    task_id: str | None = None,
    all_tasks: bool = False,
    completed_only: bool = False,
) -> int:
    """Deletes tasks from the roadmap.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: Optional ID of a specific task to delete.
        all_tasks: If True, deletes all tasks and clears context.
        completed_only: If True, deletes only completed tasks.

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
            data.context = ""
        elif completed_only:
            completed_tasks = [
                t
                for t in data.tasks
                if t.status
                in (
                    models.TaskStatus.COMPLETED,
                    models.TaskStatus.FAILED,
                    models.TaskStatus.CANCELLED,
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
                )
            ]
        elif task_id:
            tasks_to_delete = [t for t in data.tasks if t.id.startswith(task_id)]
            for t in tasks_to_delete:
                lifecycle.reset_task_logs(tasks_file, t.id)
            data.tasks = [t for t in data.tasks if not t.id.startswith(task_id)]

        persistence.save_tasks(tasks_file, data)
        return initial_count - len(data.tasks)


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
        require_progress: If True, raises ValueError if the task has no progress.
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

        # Find the task
        task_idx = -1
        target = None
        for i, t in enumerate(data.tasks):
            if t.id.startswith(task_id):
                task_idx = i
                target = t
                break

        if not target:
            raise models.TaskNotFoundError(f"Task {task_id} not found")

        if target.status == models.TaskStatus.COMPLETED and description:
            raise ValueError("Cannot edit description of a completed task")

        if require_progress and not target.progress:
            raise ValueError(
                f"Task {target.id} has no recorded progress. "
                "Record at least one progress entry before completing or failing."
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
                )
            ):
                lifecycle.update_run_time(target)
                target.requested_status = models.TaskStatus(status)
                target.last_started_at = time.time()  # Track hook execution time
            else:
                if target.status == models.TaskStatus.IN_PROGRESS:
                    lifecycle.update_run_time(target)

                target.status = models.TaskStatus(status)
                if status in (
                    models.TaskStatus.COMPLETED,
                    models.TaskStatus.FAILED,
                    models.TaskStatus.CANCELLED,
                ):
                    target.completed_at = time.time()
                    target.pid = None
                    target.last_heartbeat = None
                    target.requested_status = None
                elif status == models.TaskStatus.PENDING:
                    target.completed_at = None
                    target.attempts = 0
                    target.requested_status = None
                elif target.completed_at is not None:
                    target.completed_at = None

        if index is not None:
            task_to_move = data.tasks.pop(task_idx)
            if index == -1:
                data.tasks.append(task_to_move)
            else:
                data.tasks.insert(index, task_to_move)

        persistence.save_tasks(tasks_file, data)
    return target


def update_context(tasks_file: pathlib.Path, context: str) -> None:
    """Updates the project context.

    Args:
        tasks_file: Path to the tasks YAML file.
        context: The new project context string.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        data.context = context
        persistence.save_tasks(tasks_file, data)
