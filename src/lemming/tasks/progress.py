import pathlib

from .. import models, persistence


def add_progress(tasks_file: pathlib.Path, task_id: str, text: str):
    """Adds a progress entry to a task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task.
        text: The progress text to add.

    Returns:
        The updated Task.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise models.TaskNotFoundError(f"Task {task_id} not found")

        if text not in target.progress:
            target.progress.append(text)
        persistence.save_tasks(tasks_file, data)
    return target


def delete_progress(tasks_file: pathlib.Path, task_id: str, index: int):
    """Deletes a progress entry from a task by index.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task.
        index: The index of the progress entry to delete.

    Returns:
        The updated Task.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise models.TaskNotFoundError(f"Task {task_id} not found")

        if index < 0 or index >= len(target.progress):
            raise ValueError(
                f"Index {index} out of range (0-{len(target.progress) - 1})"
            )

        target.progress.pop(index)
        persistence.save_tasks(tasks_file, data)
    return target


def edit_progress(tasks_file: pathlib.Path, task_id: str, index: int, new_text: str):
    """Edits a progress entry for a task by index.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task.
        index: The index of the progress entry to edit.
        new_text: The new progress text.

    Returns:
        The updated Task.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise models.TaskNotFoundError(f"Task {task_id} not found")

        if index < 0 or index >= len(target.progress):
            raise ValueError(
                f"Index {index} out of range (0-{len(target.progress) - 1})"
            )

        target.progress[index] = new_text
        persistence.save_tasks(tasks_file, data)
    return target
