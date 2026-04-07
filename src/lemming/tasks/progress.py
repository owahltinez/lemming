import pathlib

from .. import models, persistence


def add_progress(tasks_file: pathlib.Path, task_id: str, text: str) -> models.Task:
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
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise ValueError(f"Task {task_id} not found")

        target.progress.append(text)
        persistence.save_tasks(tasks_file, data)
    return target
