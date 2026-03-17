import asyncio
import json
import logging
import os
import pathlib
import shutil
import subprocess
import tempfile

import pytest
import yaml
import fastapi.testclient

from lemming import api
from lemming import paths
from lemming import tasks
from lemming import utils

client = fastapi.testclient.TestClient(api.app)


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
    original_tasks_file = api.app.state.tasks_file
    api.app.state.tasks_file = test_tasks_file

    yield test_tasks_file

    # Restore the original TASKS_FILE
    api.app.state.tasks_file = original_tasks_file
    shutil.rmtree(test_dir)


@pytest.fixture
def git_repo():
    # Create a temporary directory and initialize a git repo
    test_dir = tempfile.mkdtemp()
    orig_cwd = os.getcwd()
    os.chdir(test_dir)

    # Clear cached git repo check from previous tests
    if hasattr(utils.in_git_repo, "_result"):
        del utils.in_git_repo._result

    subprocess.run(["git", "init"], check=True)
    subprocess.run(["git", "config", "user.email", "you@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Your Name"], check=True)

    # Create some files
    (pathlib.Path(test_dir) / "file1.txt").write_text("content1")
    (pathlib.Path(test_dir) / "dir1").mkdir()
    (pathlib.Path(test_dir) / "dir1" / "file2.txt").write_text("content2")

    # Create .gitignore and ignore some files
    (pathlib.Path(test_dir) / ".gitignore").write_text("ignored.txt\nnode_modules/")
    (pathlib.Path(test_dir) / "ignored.txt").write_text("should be ignored")
    (pathlib.Path(test_dir) / "node_modules").mkdir()
    (pathlib.Path(test_dir) / "node_modules" / "some_file.txt").write_text("ignored")

    yield pathlib.Path(test_dir)

    # Clear cached git repo check and restore cwd
    if hasattr(utils.in_git_repo, "_result"):
        del utils.in_git_repo._result
    os.chdir(orig_cwd)
    shutil.rmtree(test_dir)


@pytest.fixture
def non_git_dir():
    """A temporary directory that is NOT a git repo."""
    test_dir = tempfile.mkdtemp()
    orig_cwd = os.getcwd()
    os.chdir(test_dir)

    # Clear cached git repo check
    if hasattr(utils.in_git_repo, "_result"):
        del utils.in_git_repo._result

    # Create files (including one that would be "ignored" if git were present)
    (pathlib.Path(test_dir) / "file1.txt").write_text("content1")
    (pathlib.Path(test_dir) / "ignored.txt").write_text("not actually ignored")

    yield pathlib.Path(test_dir)

    if hasattr(utils.in_git_repo, "_result"):
        del utils.in_git_repo._result
    os.chdir(orig_cwd)
    shutil.rmtree(test_dir)


# --- Task API Tests ---


def test_get_data(test_tasks):
    response = client.get("/api/data")
    assert response.status_code == 200
    data = response.json()
    assert data["context"] == "Initial context"
    assert len(data["tasks"]) == 3
    # Check that task1 is in the list
    task1 = next(t for t in data["tasks"] if t["id"] == "task1")
    assert task1["status"] == "completed"
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

    async def get_first_event():
        gen = api._sse_generator()
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

    async def check_response():
        response = await api.sse_events()
        assert response.media_type == "text/event-stream"
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.headers["X-Accel-Buffering"] == "no"

    asyncio.run(check_response())


def test_build_project_data(test_tasks):
    """get_project_data returns correct structure used by both GET and SSE."""
    result = tasks.get_project_data(api.app.state.tasks_file)
    assert result["context"] == "Initial context"
    assert len(result["tasks"]) == 3
    assert "cwd" in result
    assert isinstance(result["loop_running"], bool)


def test_has_log_population(test_tasks):
    # Initially no logs
    response = client.get("/api/data")
    data = response.json()
    for task in data["tasks"]:
        assert task["has_log"] is False

    # Create a log for task1
    log_file = paths.get_log_file(api.app.state.tasks_file, "task1")
    log_file.write_text("Some logs")

    response = client.get("/api/data")
    data = response.json()
    task1 = next(t for t in data["tasks"] if t["id"] == "task1")
    assert task1["has_log"] is True

    task2 = next(t for t in data["tasks"] if t["id"] == "task2")
    assert task2["has_log"] is False


def test_quiet_poll_filter():
    """QuietPollFilter suppresses access-log lines for polling endpoints."""
    filt = api.QuietPollFilter()

    # Simulate a uvicorn access-log record for the polling endpoints.
    for path in ("/api/data", "/api/events"):
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:55964", "GET", path, "1.1", 200),
            exc_info=None,
        )
        assert filt.filter(record) is False

    # GET /api/tasks/{task_id} and GET /api/tasks/{task_id}/log should be quieted.
    for path in ("/api/tasks/abc-123", "/api/tasks/xyz-789/log"):
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:55964", "GET", path, "1.1", 200),
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
        args=("127.0.0.1:55964", "GET", "/api/agents", "1.1", 200),
        exc_info=None,
    )
    assert filt.filter(record_other) is True

    # Important: PATCH /api/tasks should NOT be quieted.
    record_patch = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:55964", "PATCH", "/api/tasks/abc-123", "1.1", 200),
        exc_info=None,
    )
    assert filt.filter(record_patch) is True


def test_get_single_task(test_tasks):
    # task1 exists in test_tasks fixture
    resp = client.get("/api/tasks/task1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "task1"
    assert resp.json()["description"] == "Completed Task"


def test_get_nonexistent_task(test_tasks):
    resp = client.get("/api/tasks/nonexistent")
    assert resp.status_code == 404


def test_serve_log_page(test_tasks):
    # Try to access the log page for task1
    resp = client.get("/tasks/task1/log")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# --- File API Tests ---


def test_root_redirect(git_repo):
    response = client.get("/files", follow_redirects=False)
    assert response.status_code in [302, 307, 301]
    assert response.headers["location"].endswith("/files/")


def test_list_root(git_repo):
    # Test template response
    response = client.get("/files/")
    assert response.status_code == 200
    assert "Lemming" in response.text

    # Test API response
    response = client.get("/api/files/")
    assert response.status_code == 200
    data = response.json()
    names = [item["name"] for item in data["contents"]]
    assert "file1.txt" in names
    assert "dir1/" in names
    assert "ignored.txt" not in names
    assert "node_modules/" not in names

    # Check metadata
    file1 = next(item for item in data["contents"] if item["name"] == "file1.txt")
    assert file1["size"] == 8  # "content1"
    assert "modified" in file1


def test_list_subdir(git_repo):
    # Test template response
    response = client.get("/files/dir1")
    assert response.status_code == 200
    assert "Lemming" in response.text

    # Test API response
    response = client.get("/api/files/dir1")
    assert response.status_code == 200
    data = response.json()
    names = [item["name"] for item in data["contents"]]
    assert "file2.txt" in names


def test_serve_file(git_repo):
    response = client.get("/files/file1.txt")
    assert response.status_code == 200
    assert response.text == "content1"


def test_serve_ignored_file(git_repo):
    response = client.get("/files/ignored.txt")
    assert response.status_code == 403
    assert "Forbidden" in response.text


def test_serve_nonexistent_file(git_repo):
    response = client.get("/files/nonexistent.txt")
    assert response.status_code == 404


def test_list_non_git_dir(non_git_dir):
    """Files should be listed without errors when not in a git repo."""
    response = client.get("/api/files/")
    assert response.status_code == 200
    data = response.json()
    names = [item["name"] for item in data["contents"]]

    # All files should be visible since there's no git to check ignore rules
    assert "file1.txt" in names
    assert "ignored.txt" in names


def test_share_token_middleware():
    # Setup test client
    original_token = getattr(api.app.state, "share_token", None)
    try:
        api.app.state.share_token = "secret123"
        client = fastapi.testclient.TestClient(api.app)

        # Missing token -> 401
        response = client.get("/api/data")
        assert response.status_code == 401

        # Valid token via query
        response = client.get("/api/data?token=secret123")
        assert response.status_code == 200
        assert "lemming_share_token=secret123" in response.headers.get("set-cookie", "")

        # Valid token via cookie
        client.cookies.set("lemming_share_token", "secret123")
        response = client.get("/api/data")
        assert response.status_code == 200

        # Local bypass via host header
        response = client.get("/api/data", headers={"host": "127.0.0.1:8999"})
        assert response.status_code == 200

        response = client.get("/api/data", headers={"host": "localhost:8999"})
        assert response.status_code == 200
    finally:
        # Restore
        api.app.state.share_token = original_token


def test_api_log(test_tasks):
    test_tasks_file = test_tasks
    original_tasks_file = api.app.state.tasks_file
    api.app.state.tasks_file = test_tasks_file
    client = fastapi.testclient.TestClient(api.app)

    task_id = "testlogid"
    log_file = paths.get_log_file(test_tasks_file, task_id)
    log_file.write_text("API log content")

    response = client.get(f"/api/tasks/{task_id}/log")
    assert response.status_code == 200
    assert response.json() == {"log": "API log content"}

    # Test non-existent log
    response = client.get("/api/tasks/missing/log")
    assert response.status_code == 200
    assert response.json() == {"log": ""}

    api.app.state.tasks_file = original_tasks_file


def test_api_delete_log_cleanup(test_tasks):
    test_tasks_file = test_tasks
    original_tasks_file = api.app.state.tasks_file
    api.app.state.tasks_file = test_tasks_file
    client = fastapi.testclient.TestClient(api.app)

    # 1. Add a task
    data = tasks.load_tasks(test_tasks_file)
    task_id = "api_delete_test"
    data["tasks"].append(
        {
            "id": task_id,
            "description": "api delete test",
            "status": "pending",
            "attempts": 0,
            "outcomes": [],
        }
    )
    tasks.save_tasks(test_tasks_file, data)

    # 2. Create log manually
    log_file = paths.get_log_file(test_tasks_file, task_id)
    log_file.write_text("API delete log")
    assert log_file.exists()

    # 3. Delete via API
    response = client.delete(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    assert not log_file.exists()

    api.app.state.tasks_file = original_tasks_file
