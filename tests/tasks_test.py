import os
import time
from unittest.mock import patch

import pytest
import yaml

from lemming import tasks


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
        task = tasks.add_task(tasks_file, "child task")
        assert task.parent == "parent123"
        assert task.parent_tasks_file == str(parent_file)

    # Manual override
    task2 = tasks.add_task(tasks_file, "another task", parent="override")
    assert task2.parent == "override"
    assert task2.parent_tasks_file is None


def test_generate_task_id():
    id1 = tasks.generate_task_id()
    id2 = tasks.generate_task_id()
    assert len(id1) == 8
    assert id1 != id2


def test_is_pid_alive():
    assert tasks.is_pid_alive(os.getpid()) is True
    assert tasks.is_pid_alive(999999) is False  # Assuming this PID doesn't exist


def test_load_save_tasks(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = tasks.Roadmap(
        context="test",
        tasks=[
            tasks.Task(
                id="1",
                description="task 1",
                status=tasks.TaskStatus.PENDING,
                attempts=0,
            )
        ],
    )
    tasks.save_tasks(tasks_file, data)

    loaded = tasks.load_tasks(tasks_file)
    assert loaded.context == "test"
    assert len(loaded.tasks) == 1
    assert loaded.tasks[0].id == "1"


def test_add_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "New task")
    assert task.description == "New task"
    assert task.status == tasks.TaskStatus.PENDING

    data = tasks.load_tasks(tasks_file)
    assert len(data.tasks) == 1


def test_claim_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Claim me")
    task_id = task.id

    claimed = tasks.claim_task(tasks_file, task_id, pid=123)
    assert claimed is not None
    assert claimed.status == tasks.TaskStatus.IN_PROGRESS
    assert claimed.pid == 123
    assert claimed.attempts == 1


def test_update_task_description(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Old description")
    task_id = task.id

    # 1. Successful update
    updated = tasks.update_task(tasks_file, task_id, description="New description")
    assert updated.description == "New description"

    # 2. Cannot edit description of a completed task
    tasks.update_task(tasks_file, task_id, status=tasks.TaskStatus.COMPLETED)

    with pytest.raises(ValueError, match="Cannot edit description of a completed task"):
        tasks.update_task(tasks_file, task_id, description="Trying to change")


def test_update_task_runner(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Task without runner")
    task_id = task.id

    assert task.runner is None

    updated = tasks.update_task(tasks_file, task_id, runner="custom-runner")
    assert updated.runner == "custom-runner"


def test_update_task_index(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks.add_task(tasks_file, "Task 0")
    tasks.add_task(tasks_file, "Task 1")
    t2 = tasks.add_task(tasks_file, "Task 2")
    task_id = t2.id

    # data.tasks is [Task 0, Task 1, Task 2]
    # Move Task 2 to index 0
    tasks.update_task(tasks_file, task_id, index=0)
    data = tasks.load_tasks(tasks_file)
    assert [t.description for t in data.tasks] == ["Task 2", "Task 0", "Task 1"]

    # Move Task 2 to the end
    tasks.update_task(tasks_file, task_id, index=-1)
    data = tasks.load_tasks(tasks_file)
    assert [t.description for t in data.tasks] == ["Task 0", "Task 1", "Task 2"]

    # Move Task 0 to index 1
    t0_id = next(t.id for t in data.tasks if t.description == "Task 0")
    tasks.update_task(tasks_file, t0_id, index=1)
    data = tasks.load_tasks(tasks_file)
    assert [t.description for t in data.tasks] == ["Task 1", "Task 0", "Task 2"]


def test_update_task_status_lifecycle(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Status lifecycle")
    task_id = task.id

    # 1. PENDING -> COMPLETED
    updated = tasks.update_task(tasks_file, task_id, status=tasks.TaskStatus.COMPLETED)
    assert updated.status == tasks.TaskStatus.COMPLETED
    assert updated.completed_at is not None

    # 2. COMPLETED -> PENDING (resets attempts and completed_at)
    updated.attempts = 5
    with tasks.lock_tasks(tasks_file):
        data = tasks.load_tasks(tasks_file)
        data.tasks[0].attempts = 5
        tasks.save_tasks(tasks_file, data)

    updated = tasks.update_task(tasks_file, task_id, status=tasks.TaskStatus.PENDING)
    assert updated.status == tasks.TaskStatus.PENDING
    assert updated.completed_at is None
    assert updated.attempts == 0

    # 3. PENDING -> FAILED
    updated = tasks.update_task(tasks_file, task_id, status=tasks.TaskStatus.FAILED)
    assert updated.status == tasks.TaskStatus.FAILED
    assert updated.completed_at is not None

    # 4. IN_PROGRESS -> REQUESTED (when running as self)
    tasks.update_task(tasks_file, task_id, status=tasks.TaskStatus.PENDING)
    tasks.claim_task(tasks_file, task_id, pid=1234)

    with patch.dict(os.environ, {"LEMMING_PARENT_TASK_ID": task_id}):
        # Request completion
        updated = tasks.update_task(
            tasks_file, task_id, status=tasks.TaskStatus.COMPLETED
        )
        assert updated.status == tasks.TaskStatus.IN_PROGRESS
        assert updated.completion_requested is True

        # Request failure
        updated = tasks.update_task(tasks_file, task_id, status=tasks.TaskStatus.FAILED)
        assert updated.status == tasks.TaskStatus.IN_PROGRESS
        assert updated.failure_requested is True
        assert updated.completion_requested is False

        # Reset to pending (clears flags)
        updated = tasks.update_task(
            tasks_file, task_id, status=tasks.TaskStatus.PENDING
        )
        assert updated.status == tasks.TaskStatus.IN_PROGRESS
        assert updated.completion_requested is False
        assert updated.failure_requested is False


def test_update_task_requires_outcomes(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Requires outcomes")
    task_id = task.id

    # Should raise error if no outcomes
    with pytest.raises(ValueError, match="has no recorded outcomes"):
        tasks.update_task(
            tasks_file, task_id, status="completed", require_outcomes=True
        )

    # Should succeed after adding an outcome
    tasks.add_outcome(tasks_file, task_id, "All good")
    updated = tasks.update_task(
        tasks_file, task_id, status="completed", require_outcomes=True
    )
    assert updated.status == tasks.TaskStatus.COMPLETED


def test_update_task_parent_fields(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Update parent fields")
    task_id = task.id

    # 1. Update with values
    updated = tasks.update_task(
        tasks_file,
        task_id,
        parent="parent123",
        parent_tasks_file="parent_tasks.yml",
    )
    assert updated.parent == "parent123"
    assert updated.parent_tasks_file == "parent_tasks.yml"

    # 2. Clear values with empty strings
    cleared = tasks.update_task(
        tasks_file,
        task_id,
        parent="",
        parent_tasks_file="",
    )
    assert cleared.parent is None
    assert cleared.parent_tasks_file is None


def test_update_task_invalid_inputs(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Invalid inputs test")
    task_id = task.id

    # 1. Invalid task ID
    with pytest.raises(ValueError, match="Task notfound-123 not found"):
        tasks.update_task(tasks_file, "notfound-123", description="Oops")

    # 2. Invalid status
    with pytest.raises(ValueError, match="'invalid-status' is not a valid TaskStatus"):
        tasks.update_task(tasks_file, task_id, status="invalid-status")


def test_add_outcome(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Outcome test")
    task_id = task.id

    tasks.add_outcome(tasks_file, task_id, "Something happened")
    data = tasks.load_tasks(tasks_file)
    assert "Something happened" in data.tasks[0].outcomes


def test_delete_outcome(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Delete outcome test")
    task_id = task.id

    tasks.add_outcome(tasks_file, task_id, "Outcome 0")
    tasks.add_outcome(tasks_file, task_id, "Outcome 1")
    tasks.add_outcome(tasks_file, task_id, "Outcome 2")

    tasks.delete_outcome(tasks_file, task_id, 1)
    data = tasks.load_tasks(tasks_file)
    assert data.tasks[0].outcomes == ["Outcome 0", "Outcome 2"]


def test_edit_outcome(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Edit outcome test")
    task_id = task.id

    tasks.add_outcome(tasks_file, task_id, "Old outcome")
    tasks.edit_outcome(tasks_file, task_id, 0, "New outcome")
    data = tasks.load_tasks(tasks_file)
    assert data.tasks[0].outcomes == ["New outcome"]


def test_reset_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Reset me")
    task_id = task.id

    tasks.update_task(
        tasks_file, task_id, status=tasks.TaskStatus.COMPLETED, require_outcomes=False
    )
    tasks.add_outcome(tasks_file, task_id, "Outcome")

    tasks.reset_task(tasks_file, task_id)
    data = tasks.load_tasks(tasks_file)
    assert data.tasks[0].status == tasks.TaskStatus.PENDING
    assert data.tasks[0].attempts == 0
    assert data.tasks[0].outcomes == []


def test_update_run_time():
    task = tasks.Task(id="1", description="test", last_started_at=time.time() - 5)
    tasks.update_run_time(task)
    assert task.run_time >= 5.0


def test_claim_already_in_progress(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Already in progress")
    task_id = task.id

    # First claim with alive PID
    claimed = tasks.claim_task(tasks_file, task_id, pid=os.getpid())
    assert claimed is not None
    assert claimed.status == tasks.TaskStatus.IN_PROGRESS

    # Second claim should fail
    claimed_again = tasks.claim_task(tasks_file, task_id, pid=456)
    assert claimed_again is None

    # But if it's stale, it should succeed
    with tasks.lock_tasks(tasks_file):
        data = tasks.load_tasks(tasks_file)
        data.tasks[0].last_heartbeat = time.time() - (tasks.STALE_THRESHOLD + 1)
        tasks.save_tasks(tasks_file, data)

    claimed_stale = tasks.claim_task(tasks_file, task_id, pid=789)
    assert claimed_stale is not None
    assert claimed_stale.pid == 789
    assert claimed_stale.attempts == 2


def test_get_project_data_deduplication(tmp_path):
    tasks_file = tmp_path / "tasks.yml"

    # Create a corrupted roadmap with duplicate task IDs
    data = tasks.Roadmap(
        context="test",
        tasks=[
            tasks.Task(id="1", description="Task 1", status=tasks.TaskStatus.PENDING),
            tasks.Task(
                id="1", description="Task 1 Duplicate", status=tasks.TaskStatus.PENDING
            ),
            tasks.Task(id="2", description="Task 2", status=tasks.TaskStatus.PENDING),
        ],
    )
    tasks.save_tasks(tasks_file, data)

    project_data = tasks.get_project_data(tasks_file)

    # Should only have two unique tasks, newer first
    assert len(project_data.tasks) == 2
    assert project_data.tasks[0].description == "Task 2"
    assert project_data.tasks[1].description == "Task 1"


def test_loop_lock_management(tmp_path):
    tasks_file = tmp_path / "tasks.yml"

    # Initially, no loop is running
    assert tasks.get_loop_pid(tasks_file) is None
    assert tasks.is_loop_running(tasks_file) is False

    # Create a lock file
    tasks.acquire_loop_lock(tasks_file)
    pid = tasks.get_loop_pid(tasks_file)
    assert pid == os.getpid()
    assert tasks.is_loop_running(tasks_file) is True

    # Release the lock
    tasks.release_loop_lock(tasks_file)
    assert tasks.get_loop_pid(tasks_file) is None
    assert tasks.is_loop_running(tasks_file) is False


def test_is_loop_running_stale_pid(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    lock_path = tasks._get_loop_lock_path(tasks_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Write a PID that is likely not alive (e.g., 999999)
    lock_path.write_text("999999")
    assert tasks.get_loop_pid(tasks_file) == 999999
    assert tasks.is_loop_running(tasks_file) is False


def test_get_loop_pid_corrupted_lock_file(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    lock_path = tasks._get_loop_lock_path(tasks_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Write "corrupted" content
    lock_path.write_text("not-a-pid")
    assert tasks.get_loop_pid(tasks_file) is None
    assert tasks.is_loop_running(tasks_file) is False


def test_save_tasks_excludes_computed_fields(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.Task(
        id="123",
        description="Test Task",
        index=5,
        has_runner_log=True,
    )
    roadmap = tasks.Roadmap(tasks=[task])

    tasks.save_tasks(tasks_file, roadmap)

    # Read the raw YAML to verify exclusion
    with open(tasks_file, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f)

    task_data = raw_data["tasks"][0]
    assert "id" in task_data
    assert "description" in task_data
    # Computed fields should be excluded
    assert "index" not in task_data
    assert "has_runner_log" not in task_data


def test_get_project_data_enriches_metadata(tmp_path):
    from lemming import paths

    tasks_file = tmp_path / "tasks.yml"
    tasks.add_task(tasks_file, "Task 1")
    task = tasks.add_task(tasks_file, "Task 2")

    # Create a dummy log file for Task 2
    log_file = paths.get_log_file(tasks_file, task.id)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("dummy log")

    project_data = tasks.get_project_data(tasks_file)

    # Check index and has_runner_log
    # Note: get_project_data sorts tasks newest first by default.
    # Task 2 (index 1) will be first, Task 1 (index 0) second.
    t2 = next(t for t in project_data.tasks if t.id == task.id)
    t1 = next(t for t in project_data.tasks if t.id != task.id)

    assert t1.index == 0
    assert t1.has_runner_log is False

    assert t2.index == 1
    assert t2.has_runner_log is True
