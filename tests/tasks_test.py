import os
from unittest.mock import patch

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
    import os

    assert tasks.is_pid_alive(os.getpid()) is True
    assert tasks.is_pid_alive(999999) is False  # Assuming this PID doesn't exist


def test_load_save_tasks(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = tasks.Roadmap(
        context="test",
        tasks=[tasks.Task(id="1", description="task 1", status="pending", attempts=0)],
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
    assert task.status == "pending"

    data = tasks.load_tasks(tasks_file)
    assert len(data.tasks) == 1


def test_claim_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Claim me")
    task_id = task.id

    claimed = tasks.claim_task(tasks_file, task_id, pid=123)
    assert claimed is not None
    assert claimed.status == "in_progress"
    assert claimed.pid == 123
    assert claimed.attempts == 1


def test_update_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Update me")
    task_id = task.id

    updated = tasks.update_task(tasks_file, task_id, description="Updated")
    assert updated.description == "Updated"


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

    tasks.update_task(tasks_file, task_id, status="completed", require_outcomes=False)
    tasks.add_outcome(tasks_file, task_id, "Outcome")

    tasks.reset_task(tasks_file, task_id)
    data = tasks.load_tasks(tasks_file)
    assert data.tasks[0].status == "pending"
    assert data.tasks[0].attempts == 0
    assert data.tasks[0].outcomes == []


def test_update_run_time():
    import time

    task = tasks.Task(id="1", description="test", last_started_at=time.time() - 5)
    tasks.update_run_time(task)
    assert task.run_time >= 5.0


def test_claim_already_in_progress(tmp_path):
    import os

    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Already in progress")
    task_id = task.id

    # First claim with alive PID
    claimed = tasks.claim_task(tasks_file, task_id, pid=os.getpid())
    assert claimed is not None
    assert claimed.status == "in_progress"

    # Second claim should fail
    claimed_again = tasks.claim_task(tasks_file, task_id, pid=456)
    assert claimed_again is None

    # But if it's stale, it should succeed
    import time

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
            tasks.Task(id="1", description="Task 1", status="pending"),
            tasks.Task(id="1", description="Task 1 Duplicate", status="pending"),
            tasks.Task(id="2", description="Task 2", status="pending"),
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
