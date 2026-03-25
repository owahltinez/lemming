import pathlib
import shutil
import tempfile
from unittest.mock import patch

import fastapi.testclient
import pytest

from lemming import api
from lemming import tasks

client = fastapi.testclient.TestClient(api.app)


@pytest.fixture
def test_tasks():
    # Create a temporary directory and a tasks file
    test_dir = tempfile.mkdtemp()
    test_tasks_file = pathlib.Path(test_dir) / "tasks_test.yml"

    # Scaffold a valid file
    data = tasks.Roadmap(
        context="Initial context",
        tasks=[],
    )
    tasks.save_tasks(test_tasks_file, data)

    # Override the TASKS_FILE and root in the api module
    original_tasks_file = api.app.state.tasks_file
    original_root = api.app.state.root
    original_auto_start = api.app.state.disable_auto_start
    api.app.state.tasks_file = test_tasks_file
    api.app.state.root = pathlib.Path(test_dir).resolve()
    api.app.state.disable_auto_start = False

    yield test_tasks_file

    # Restore the originals
    api.app.state.tasks_file = original_tasks_file
    api.app.state.root = original_root
    api.app.state.disable_auto_start = original_auto_start
    shutil.rmtree(test_dir)


def test_add_task_auto_starts_loop(test_tasks):
    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return False
        with patch("lemming.tasks.is_loop_running", return_value=False):
            response = client.post("/api/tasks", json={"description": "New task"})
            assert response.status_code == 200

            # Verify Popen was called to start the loop
            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            cmd = args[0]
            assert "run" in cmd
            assert str(test_tasks) in cmd


def test_add_task_does_not_restart_if_running(test_tasks):
    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return True
        with patch("lemming.tasks.is_loop_running", return_value=True):
            response = client.post("/api/tasks", json={"description": "New task"})
            assert response.status_code == 200

            # Verify Popen was NOT called
            mock_popen.assert_not_called()


def test_update_task_to_pending_auto_starts_loop(test_tasks):
    # Add a completed task
    task = tasks.add_task(test_tasks, "Completed task")
    tasks.add_outcome(test_tasks, task.id, "Done")
    tasks.update_task(test_tasks, task.id, status="completed")

    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return False
        with patch("lemming.tasks.is_loop_running", return_value=False):
            # Update to pending
            response = client.patch(f"/api/tasks/{task.id}", json={"status": "pending"})
            assert response.status_code == 200

            # Verify Popen was called
            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            assert "run" in args[0]


def test_clear_task_auto_starts_loop(test_tasks):
    # Add a completed task
    task = tasks.add_task(test_tasks, "Completed task")
    tasks.add_outcome(test_tasks, task.id, "Done")
    tasks.update_task(test_tasks, task.id, status="completed")

    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return False
        with patch("lemming.tasks.is_loop_running", return_value=False):
            # Clear task
            response = client.post(f"/api/tasks/{task.id}/clear")
            assert response.status_code == 200

            # Verify Popen was called
            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            assert "run" in args[0]


def test_add_task_respects_disable_auto_start(test_tasks):
    api.app.state.disable_auto_start = True
    with patch("subprocess.Popen") as mock_popen:
        with patch("lemming.tasks.is_loop_running", return_value=False):
            response = client.post(
                "/api/tasks", json={"description": "No auto-start task"}
            )
            assert response.status_code == 200

            # Verify Popen was NOT called
            mock_popen.assert_not_called()
