import pathlib

from .. import models, persistence


def add_outcome(tasks_file: pathlib.Path, task_id: str, text: str):
    """Adds an outcome to a task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task.
        text: The outcome text to add.

    Returns:
        The updated Task.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise models.TaskNotFoundError(f"Task {task_id} not found")

        if text not in target.outcomes:
            target.outcomes.append(text)
        persistence.save_tasks(tasks_file, data)
    return target


def delete_outcome(tasks_file: pathlib.Path, task_id: str, index: int):
    """Deletes an outcome from a task by index.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task.
        index: The index of the outcome to delete.

    Returns:
        The updated Task.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise models.TaskNotFoundError(f"Task {task_id} not found")

        if index < 0 or index >= len(target.outcomes):
            raise ValueError(
                f"Index {index} out of range (0-{len(target.outcomes) - 1})"
            )

        target.outcomes.pop(index)
        persistence.save_tasks(tasks_file, data)
    return target


def edit_outcome(tasks_file: pathlib.Path, task_id: str, index: int, new_text: str):
    """Edits an outcome for a task by index.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task.
        index: The index of the outcome to edit.
        new_text: The new outcome text.

    Returns:
        The updated Task.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise models.TaskNotFoundError(f"Task {task_id} not found")

        if index < 0 or index >= len(target.outcomes):
            raise ValueError(
                f"Index {index} out of range (0-{len(target.outcomes) - 1})"
            )

        target.outcomes[index] = new_text
        persistence.save_tasks(tasks_file, data)
    return target
