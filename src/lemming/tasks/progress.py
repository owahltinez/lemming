"""Recording of task progress entries."""

import pathlib

from .. import models, persistence
from . import limits, queries


def add_progress(
    tasks_file: pathlib.Path, task_id: str, text: str
) -> models.Task:
    """Adds a progress entry to a task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task to add progress to.
        text: The progress text to add.

    Returns:
        The updated Task.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = queries.resolve_task(data.tasks, task_id)

        limits.validate_progress_entry(text)
        target.progress.append(text)
        persistence.save_tasks(tasks_file, data)
    return target
