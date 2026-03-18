from lemming import tasks


def test_load_save_tasks(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = {
        "context": "test",
        "tasks": [
            {"id": "1", "description": "task 1", "status": "pending", "attempts": 0}
        ],
    }
    tasks.save_tasks(tasks_file, data)

    loaded = tasks.load_tasks(tasks_file)
    assert loaded["context"] == "test"
    assert len(loaded["tasks"]) == 1
    assert loaded["tasks"][0]["id"] == "1"


def test_add_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "New task")
    assert task["description"] == "New task"
    assert task["status"] == "pending"

    data = tasks.load_tasks(tasks_file)
    assert len(data["tasks"]) == 1


def test_claim_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Claim me")
    task_id = task["id"]

    claimed = tasks.claim_task(tasks_file, task_id, pid=123)
    assert claimed is not None
    assert claimed["status"] == "in_progress"
    assert claimed["pid"] == 123
    assert claimed["attempts"] == 1


def test_update_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Update me")
    task_id = task["id"]

    updated = tasks.update_task(tasks_file, task_id, description="Updated")
    assert updated["description"] == "Updated"


def test_add_outcome(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Outcome test")
    task_id = task["id"]

    tasks.add_outcome(tasks_file, task_id, "Something happened")
    data = tasks.load_tasks(tasks_file)
    assert "Something happened" in data["tasks"][0]["outcomes"]


def test_reset_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Reset me")
    task_id = task["id"]

    tasks.update_task(tasks_file, task_id, status="completed", require_outcomes=False)
    tasks.add_outcome(tasks_file, task_id, "Outcome")

    tasks.reset_task(tasks_file, task_id)
    data = tasks.load_tasks(tasks_file)
    assert data["tasks"][0]["status"] == "pending"
    assert data["tasks"][0]["attempts"] == 0
    assert data["tasks"][0]["outcomes"] == []


def test_claim_already_in_progress(tmp_path):
    import os
    tasks_file = tmp_path / "tasks.yml"
    task = tasks.add_task(tasks_file, "Already in progress")
    task_id = task["id"]

    # First claim with alive PID
    claimed = tasks.claim_task(tasks_file, task_id, pid=os.getpid())
    assert claimed is not None
    assert claimed["status"] == "in_progress"

    # Second claim should fail
    claimed_again = tasks.claim_task(tasks_file, task_id, pid=456)
    assert claimed_again is None

    # But if it's stale, it should succeed
    import time
    from lemming import utils
    with utils.lock_tasks(tasks_file):
        data = tasks.load_tasks(tasks_file)
        data["tasks"][0]["last_heartbeat"] = time.time() - (utils.STALE_THRESHOLD + 1)
        tasks.save_tasks(tasks_file, data)

    claimed_stale = tasks.claim_task(tasks_file, task_id, pid=789)
    assert claimed_stale is not None
    assert claimed_stale["pid"] == 789
    assert claimed_stale["attempts"] == 2
