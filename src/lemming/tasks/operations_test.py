import os
import time
from unittest.mock import patch

import pytest

from .. import models, persistence
from . import operations


def test_add_task_captures_parent_project(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    parent_file = tmp_path / "parent_tasks.yml"

    with patch.dict(
        os.environ,
        {
            "LEMMING_PARENT_TASK_ID": "parent123",
            "LEMMING_PARENT_TASKS_FILE": str(parent_file),
        },
    ):
        task = operations.add_task(tasks_file, "child task")
        assert task.parent == "parent123"
        assert task.parent_tasks_file == str(parent_file)

    # Manual override
    task2 = operations.add_task(tasks_file, "another task", parent="override")
    assert task2.parent == "override"
    assert task2.parent_tasks_file is None


def test_add_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    now = time.time()
    task = operations.add_task(tasks_file, "New task")
    assert task.description == "New task"
    assert task.status == models.TaskStatus.PENDING
    assert task.created_at >= now

    data = persistence.load_tasks(tasks_file)
    assert len(data.tasks) == 1
    assert data.tasks[0].created_at == task.created_at


def test_update_task_description(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Old description")
    task_id = task.id

    # 1. Successful update
    updated = operations.update_task(tasks_file, task_id, description="New description")
    assert updated.description == "New description"

    # 2. Cannot edit description of a completed task
    operations.update_task(tasks_file, task_id, status=models.TaskStatus.COMPLETED)

    with pytest.raises(ValueError, match="Cannot edit description of a completed task"):
        operations.update_task(tasks_file, task_id, description="Trying to change")


def test_update_task_runner(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Task without runner")
    task_id = task.id

    assert task.runner is None

    updated = operations.update_task(tasks_file, task_id, runner="custom-runner")
    assert updated.runner == "custom-runner"


def test_update_task_index(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    operations.add_task(tasks_file, "Task 0")
    operations.add_task(tasks_file, "Task 1")
    t2 = operations.add_task(tasks_file, "Task 2")
    task_id = t2.id

    # data.tasks is [Task 0, Task 1, Task 2]
    # Move Task 2 to index 0
    operations.update_task(tasks_file, task_id, index=0)
    data = persistence.load_tasks(tasks_file)
    assert [t.description for t in data.tasks] == ["Task 2", "Task 0", "Task 1"]

    # Move Task 2 to the end
    operations.update_task(tasks_file, task_id, index=-1)
    data = persistence.load_tasks(tasks_file)
    assert [t.description for t in data.tasks] == ["Task 0", "Task 1", "Task 2"]


def test_update_task_status_lifecycle(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Status lifecycle")
    task_id = task.id

    # 1. PENDING -> COMPLETED
    updated = operations.update_task(
        tasks_file, task_id, status=models.TaskStatus.COMPLETED
    )
    assert updated.status == models.TaskStatus.COMPLETED
    assert updated.completed_at is not None

    # 2. COMPLETED -> PENDING (resets attempts and completed_at)
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        data.tasks[0].attempts = 5
        persistence.save_tasks(tasks_file, data)

    updated = operations.update_task(
        tasks_file, task_id, status=models.TaskStatus.PENDING
    )
    assert updated.status == models.TaskStatus.PENDING
    assert updated.completed_at is None
    assert updated.attempts == 0


def test_update_task_parent_fields(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Update parent fields")
    task_id = task.id

    # 1. Update with values
    updated = operations.update_task(
        tasks_file,
        task_id,
        parent="parent123",
        parent_tasks_file="parent_tasks.yml",
    )
    assert updated.parent == "parent123"
    assert updated.parent_tasks_file == "parent_tasks.yml"

    # 2. Clear values with empty strings
    cleared = operations.update_task(
        tasks_file,
        task_id,
        parent="",
        parent_tasks_file="",
    )
    assert cleared.parent is None
    assert cleared.parent_tasks_file is None


def test_delete_tasks(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    operations.add_task(tasks_file, "Task 1")
    t2 = operations.add_task(tasks_file, "Task 2")

    count = operations.delete_tasks(tasks_file, task_id=t2.id)
    assert count == 1
    data = persistence.load_tasks(tasks_file)
    assert len(data.tasks) == 1
    assert data.tasks[0].description == "Task 1"


def test_update_context(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    operations.update_context(tasks_file, "new context")
    data = persistence.load_tasks(tasks_file)
    assert data.context == "new context"
