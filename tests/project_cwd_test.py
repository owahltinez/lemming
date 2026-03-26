from unittest.mock import patch
import pathlib
import shutil
import tempfile

import pytest
import fastapi.testclient

from lemming import api
from lemming import tasks

client = fastapi.testclient.TestClient(api.app)


@pytest.fixture
def test_workspace():
    # Create a temporary root directory
    root_dir = pathlib.Path(tempfile.mkdtemp()).resolve()

    # Create a subproject directory
    subproject_dir = root_dir / "my-subproject"
    subproject_dir.mkdir()

    # Set up some tasks in the subproject
    sub_tasks_file = subproject_dir / "tasks.yml"
    data = tasks.Roadmap(
        context="Subproject context",
        tasks=[
            tasks.Task(id="sub1", description="Sub Task 1", status="pending"),
        ],
    )
    tasks.save_tasks(sub_tasks_file, data)

    # Override app state
    original_root = api.app.state.root
    original_tasks_file = api.app.state.tasks_file
    original_auto_start = api.app.state.disable_auto_start

    api.app.state.root = root_dir
    api.app.state.tasks_file = root_dir / "tasks.yml"
    api.app.state.disable_auto_start = False  # Enable auto-start for testing

    yield root_dir, subproject_dir

    # Restore app state
    api.app.state.root = original_root
    api.app.state.tasks_file = original_tasks_file
    api.app.state.disable_auto_start = original_auto_start
    shutil.rmtree(root_dir)


def test_run_loop_with_project_cwd(test_workspace):
    root_dir, subproject_dir = test_workspace

    with patch("subprocess.Popen") as mock_popen:
        response = client.post(
            "/api/run",
            json={"runner": "echo", "retries": 1},
            params={"project": "my-subproject"},
        )
        assert response.status_code == 200

        # Verify Popen was called with the correct cwd
        _, kwargs = mock_popen.call_args
        assert str(kwargs["cwd"]) == str(subproject_dir)


def test_add_task_starts_loop_with_cwd(test_workspace):
    root_dir, subproject_dir = test_workspace

    with patch("subprocess.Popen") as mock_popen:
        # Adding a task should trigger auto-start of the loop
        response = client.post(
            "/api/tasks",
            json={"description": "New task in subproject"},
            params={"project": "my-subproject"},
        )
        assert response.status_code == 200

        # Verify Popen was called with the correct cwd for the auto-started loop
        _, kwargs = mock_popen.call_args
        assert str(kwargs["cwd"]) == str(subproject_dir)


def test_update_task_starts_loop_with_cwd(test_workspace):
    root_dir, subproject_dir = test_workspace

    # Add an outcome first so update to pending doesn't fail
    sub_tasks_file = subproject_dir / "tasks.yml"
    tasks.add_outcome(sub_tasks_file, "sub1", "Did some work")

    with patch("subprocess.Popen") as mock_popen:
        # Updating a task to pending should trigger auto-start
        response = client.patch(
            "/api/tasks/sub1",
            json={"status": "pending"},
            params={"project": "my-subproject"},
        )
        assert response.status_code == 200

        # Verify Popen was called with the correct cwd
        _, kwargs = mock_popen.call_args
        assert str(kwargs["cwd"]) == str(subproject_dir)


def test_clear_task_starts_loop_with_cwd(test_workspace):
    root_dir, subproject_dir = test_workspace

    # First, make the task "failed" so it can be cleared
    sub_tasks_file = subproject_dir / "tasks.yml"
    data = tasks.load_tasks(sub_tasks_file)
    data.tasks[0].status = "failed"
    tasks.save_tasks(sub_tasks_file, data)

    with patch("subprocess.Popen") as mock_popen:
        response = client.post(
            "/api/tasks/sub1/clear",
            params={"project": "my-subproject"},
        )
        assert response.status_code == 200

        # Verify Popen was called with the correct cwd
        _, kwargs = mock_popen.call_args
        assert str(kwargs["cwd"]) == str(subproject_dir)
