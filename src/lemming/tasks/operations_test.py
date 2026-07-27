import os
import time
from unittest.mock import patch

import pytest

from .. import models, paths, persistence
from . import lifecycle, limits, operations, queries


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
    # Scaffold tasks file
    persistence.save_tasks(tasks_file, models.Roadmap())

    now = time.time()
    task = operations.add_task(tasks_file, "New task")
    assert task.description == "New task"
    assert task.status == models.TaskStatus.PENDING
    assert task.created_at >= now

    data = persistence.load_tasks(tasks_file)
    assert len(data.tasks) == 1
    assert data.tasks[0].created_at == task.created_at


def test_add_task_enforces_description_size_limit(tmp_path, monkeypatch):
    lemming_home = tmp_path / "lemming-home"
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))
    tasks_file = tmp_path / "project" / "tasks.yml"

    accepted = operations.add_task(
        tasks_file,
        "a" * limits.MAX_TASK_DESCRIPTION_CHARS,
    )
    assert len(accepted.description) == limits.MAX_TASK_DESCRIPTION_CHARS

    with pytest.raises(ValueError) as excinfo:
        operations.add_task(
            tasks_file,
            "b" * (limits.MAX_TASK_DESCRIPTION_CHARS + 1),
        )

    message = str(excinfo.value)
    assert "2,001 characters (limit 2,000)" in message
    assert "move shared rules to the long-term goal" in message
    assert str(paths.get_project_dir(tasks_file)) in message
    assert len(persistence.load_tasks(tasks_file).tasks) == 1


def test_add_task_does_not_reuse_retained_log_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    retained_log = paths.get_log_file(tasks_file, "deadbeef")
    retained_log.write_text("retained")

    with patch(
        "lemming.tasks.operations.lifecycle.generate_task_id",
        side_effect=["deadbeef", "cafebabe"],
    ):
        task = operations.add_task(tasks_file, "New task")

    assert task.id == "cafebabe"
    assert retained_log.read_text() == "retained"


def test_update_task_description(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Old description")
    task_id = task.id

    # 1. Successful update
    updated = operations.update_task(
        tasks_file, task_id, description="New description"
    )
    assert updated.description == "New description"

    # 2. Cannot edit description of a completed task
    operations.update_task(
        tasks_file, task_id, status=models.TaskStatus.COMPLETED
    )

    with pytest.raises(
        ValueError, match="Cannot edit description of a completed task"
    ):
        operations.update_task(
            tasks_file, task_id, description="Trying to change"
        )


def test_update_task_rejects_oversized_description_without_mutating(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Original description")

    with pytest.raises(ValueError, match=r"2,001 characters \(limit 2,000\)"):
        operations.update_task(
            tasks_file,
            task.id,
            description="x" * (limits.MAX_TASK_DESCRIPTION_CHARS + 1),
        )

    persisted = persistence.load_tasks(tasks_file).tasks[0]
    assert persisted.description == "Original description"


def test_legacy_oversized_content_remains_readable_and_updatable(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    legacy_description = "d" * (limits.MAX_TASK_DESCRIPTION_CHARS + 1)
    legacy_progress = "p" * (limits.MAX_PROGRESS_ENTRY_CHARS + 1)
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="legacy",
                    description=legacy_description,
                    progress=[legacy_progress],
                )
            ]
        ),
    )

    loaded = persistence.load_tasks(tasks_file)
    assert loaded.tasks[0].description == legacy_description
    updated = operations.update_task(tasks_file, "legacy", runner="claude")

    assert updated.runner == "claude"
    persisted = persistence.load_tasks(tasks_file).tasks[0]
    assert persisted.description == legacy_description
    assert persisted.progress == [legacy_progress]


def test_update_task_runner(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Task without runner")
    task_id = task.id

    assert task.runner is None

    updated = operations.update_task(
        tasks_file, task_id, runner="custom-runner"
    )
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


def test_index_matches_displayed_queue_order(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(id="p1", description="Pending 1"),
                models.Task(id="done", description="Done", status="completed"),
                models.Task(id="p2", description="Pending 2"),
                models.Task(
                    id="active", description="Active", status="in_progress"
                ),
                models.Task(id="p3", description="Pending 3"),
            ]
        ),
    )

    with patch.object(lifecycle, "generate_task_id", return_value="new"):
        operations.add_task(tasks_file, "New", index=3)

    project = queries.get_project_data(tasks_file)
    queue_ids = [
        task.id
        for task in project.tasks
        if task.status not in operations._DONE_STATUSES
    ]
    assert queue_ids == ["active", "p1", "p2", "new", "p3"]

    operations.update_task(tasks_file, "new", index=2)
    project = queries.get_project_data(tasks_file)
    queue_ids = [
        task.id
        for task in project.tasks
        if task.status not in operations._DONE_STATUSES
    ]
    assert queue_ids == ["active", "p1", "new", "p2", "p3"]


def test_index_rejects_positions_that_cannot_be_displayed(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="active", description="Active", status="in_progress"
                ),
                models.Task(id="pending", description="Pending"),
            ]
        ),
    )

    with pytest.raises(ValueError, match="before 1 in-progress"):
        operations.update_task(tasks_file, "pending", index=0)
    with pytest.raises(ValueError, match="maximum 2"):
        operations.add_task(tasks_file, "Too far", index=3)


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


def test_delete_tasks_retains_runner_log(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    operations.add_task(tasks_file, "Task 1")
    t2 = operations.add_task(tasks_file, "Task 2")
    log_file = paths.get_log_file(tasks_file, t2.id)
    log_file.write_text("runner output")

    count = operations.delete_tasks(tasks_file, task_id=t2.id, force=True)
    assert count == 1
    data = persistence.load_tasks(tasks_file)
    assert len(data.tasks) == 1
    assert data.tasks[0].description == "Task 1"
    assert log_file.read_text() == "runner output"


def test_delete_started_task_requires_explicit_force(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Started task")
    operations.update_task(
        tasks_file, task.id, status=models.TaskStatus.IN_PROGRESS
    )

    with pytest.raises(ValueError, match="Supersede it"):
        operations.delete_tasks(tasks_file, task_id=task.id)

    assert persistence.load_tasks(tasks_file).tasks[0].id == task.id


def test_supersede_task_preserves_history(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Large task")
    operations.update_task(
        tasks_file, task.id, status=models.TaskStatus.IN_PROGRESS
    )
    log_file = paths.get_log_file(tasks_file, task.id)
    log_file.write_text("partial work")

    superseded = operations.supersede_task(
        tasks_file, task.id, "split after reaching the time limit"
    )

    assert superseded.status == models.TaskStatus.SUPERSEDED
    assert superseded.superseded_at is not None
    assert superseded.superseded_reason == "split after reaching the time limit"
    assert superseded.requested_status is None
    assert log_file.read_text() == "partial work"


def test_delete_all_tasks_removes_runner_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Task")
    log_file = paths.get_log_file(tasks_file, task.id)
    log_file.write_text("runner output")

    operations.delete_tasks(tasks_file, all_tasks=True)

    assert not log_file.exists()


def test_update_task_rejects_ambiguous_prefix(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(id="abc12345", description="First"),
                models.Task(id="abc67890", description="Second"),
            ]
        ),
    )

    with pytest.raises(models.AmbiguousTaskIdError, match="abc12345, abc67890"):
        operations.update_task(tasks_file, "abc", description="Updated")


def test_delete_tasks_rejects_ambiguous_prefix(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(id="abc12345", description="First"),
                models.Task(id="abc67890", description="Second"),
            ]
        ),
    )

    with pytest.raises(models.AmbiguousTaskIdError, match="abc12345, abc67890"):
        operations.delete_tasks(tasks_file, task_id="abc")

    data = persistence.load_tasks(tasks_file)
    assert [task.id for task in data.tasks] == ["abc12345", "abc67890"]


def test_update_goal(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    operations.update_goal(tasks_file, "new goal")
    data = persistence.load_tasks(tasks_file)
    assert data.goal == "new goal"
