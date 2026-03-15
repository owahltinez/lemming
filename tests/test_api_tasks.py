import pathlib
import shutil
import tempfile
import yaml
from fastapi.testclient import TestClient
import pytest

# Ensure PYTHONPATH includes src
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

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
    assert response.json()["attempts"] == 0

    with open(test_tasks, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        task1 = next(t for t in data["tasks"] if t["id"] == "task1")
        assert task1["status"] == "pending"
        assert task1["attempts"] == 0


def test_sse_generator_initial_event(test_tasks):
    """SSE generator yields an initial event with current task data."""
    import asyncio
    import json
    from lemming.api import _sse_generator

    async def get_first_event():
        gen = _sse_generator()
        return await gen.__anext__()

    first_event = asyncio.run(get_first_event())
    assert first_event.startswith("data: ")
    assert first_event.endswith("\n\n")

    payload = json.loads(first_event.removeprefix("data: ").strip())
    assert "tasks" in payload
    assert "context" in payload
    assert "cwd" in payload
    assert "loop_running" in payload
    assert len(payload["tasks"]) == 3
    assert payload["context"] == "Initial context"


def test_sse_events_endpoint_returns_event_stream(test_tasks):
    """SSE endpoint returns correct content-type and headers."""
    from lemming.api import sse_events

    import asyncio

    async def check_response():
        response = await sse_events()
        assert response.media_type == "text/event-stream"
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.headers["X-Accel-Buffering"] == "no"

    asyncio.run(check_response())


def test_build_project_data(test_tasks):
    """_build_project_data returns correct structure used by both GET and SSE."""
    from lemming.api import _build_project_data

    result = _build_project_data()
    assert result["context"] == "Initial context"
    assert len(result["tasks"]) == 3
    assert "cwd" in result
    assert isinstance(result["loop_running"], bool)


def test_quiet_poll_filter():
    """QuietPollFilter suppresses access-log lines for polling endpoints."""
    import logging
    from lemming.api import QuietPollFilter

    filt = QuietPollFilter()

    # Simulate a uvicorn access-log record for the polling endpoint.
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:55964", "GET", "/api/data", "1.1", 200),
        exc_info=None,
    )
    assert filt.filter(record) is False

    # Non-polling endpoints should still be logged.
    record_other = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:55964", "GET", "/api/tasks", "1.1", 200),
        exc_info=None,
    )
    assert filt.filter(record_other) is True
