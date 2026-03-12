import pathlib
import shutil
import tempfile
import yaml
from fastapi.testclient import TestClient
import pytest

# Ensure PYTHONPATH includes src
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from lemming import api
from lemming.api import app


@pytest.fixture
def test_tasks():
    # Create a temporary directory and a tasks file
    test_dir = tempfile.mkdtemp()
    test_tasks_file = pathlib.Path(test_dir) / "tasks_test.yml"

    # Scaffold a valid file
    data = {
        "context": "Initial context",
        "tasks": [
            {
                "id": "task1",
                "description": "Completed Task",
                "status": "completed",
                "attempts": 1,
                "outcomes": ["All good"],
            },
            {
                "id": "task2",
                "description": "Pending Task",
                "status": "pending",
                "attempts": 0,
                "outcomes": [],
            },
            {
                "id": "task3",
                "description": "In Progress Task",
                "status": "in_progress",
                "attempts": 1,
                "outcomes": [],
            },
        ],
    }
    with open(test_tasks_file, "w", encoding="utf-8") as f:
        yaml.dump(data, f)

    # Override the TASKS_FILE in the api module
    original_tasks_file = app.state.tasks_file
    app.state.tasks_file = test_tasks_file

    yield test_tasks_file

    # Restore the original TASKS_FILE
    app.state.tasks_file = original_tasks_file
    shutil.rmtree(test_dir)


client = TestClient(app)


def test_get_data(test_tasks):
    response = client.get("/api/data")
    assert response.status_code == 200
    data = response.json()
    assert data["context"] == "Initial context"
    assert len(data["tasks"]) == 3
    assert data["tasks"][0]["id"] == "task1"
    assert data["tasks"][0]["status"] == "completed"
    assert data["loop_running"] is True


def test_delete_completed_tasks(test_tasks):
    response = client.delete("/api/tasks/completed")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # Verify tasks in file
    with open(test_tasks, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        task_ids = [t["id"] for t in data["tasks"]]
        assert "task1" not in task_ids
        assert "task2" in task_ids
        assert "task3" in task_ids
        assert len(data["tasks"]) == 2


def test_delete_specific_task(test_tasks):
    response = client.delete("/api/tasks/task2")
    assert response.status_code == 200

    with open(test_tasks, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        task_ids = [t["id"] for t in data["tasks"]]
        assert "task2" not in task_ids
        assert len(data["tasks"]) == 2


def test_update_task_description(test_tasks):
    response = client.patch(
        "/api/tasks/task2", json={"description": "Updated Pending Task"}
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Updated Pending Task"

    with open(test_tasks, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        task2 = next(t for t in data["tasks"] if t["id"] == "task2")
        assert task2["description"] == "Updated Pending Task"


def test_update_completed_task_description_fails(test_tasks):
    response = client.patch(
        "/api/tasks/task1", json={"description": "Attempt to update completed task"}
    )
    assert response.status_code == 400
    assert "Cannot edit description of a completed task" in response.json()["detail"]


def test_uncomplete_task_via_api(test_tasks):
    # Updating status of a completed task should still be allowed
    response = client.patch("/api/tasks/task1", json={"status": "pending"})
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    with open(test_tasks, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        task1 = next(t for t in data["tasks"] if t["id"] == "task1")
        assert task1["status"] == "pending"
