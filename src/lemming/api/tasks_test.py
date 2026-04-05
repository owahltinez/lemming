from unittest.mock import patch
from lemming import tasks
from lemming import api
from lemming import paths


def test_get_data(client, test_tasks):
    response = client.get("/api/data")
    assert response.status_code == 200
    data = response.json()
    assert data["context"] == "Initial context"
    assert len(data["tasks"]) == 3
    # Check that task1 is in the list
    task1 = next(t for t in data["tasks"] if t["id"] == "task1")
    assert task1["status"] == tasks.TaskStatus.COMPLETED
    assert data["loop_running"] is True


def test_add_task(client, test_tasks):
    response = client.post("/api/tasks", json={"description": "New task from test"})
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "New task from test"
    assert data["id"]  # id should be auto-generated
    assert data["status"] == tasks.TaskStatus.PENDING

    # Verify task was persisted
    roadmap = tasks.load_tasks(test_tasks)
    assert any(t.description == "New task from test" for t in roadmap.tasks)


def test_add_task_with_runner(client, test_tasks):
    response = client.post(
        "/api/tasks", json={"description": "Runner task", "runner": "claude"}
    )
    assert response.status_code == 200
    assert response.json()["runner"] == "claude"


def test_delete_completed_tasks(client, test_tasks):
    response = client.post("/api/tasks/delete-completed")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # Verify tasks in file
    data = tasks.load_tasks(test_tasks)
    task_ids = [t.id for t in data.tasks]
    assert "task1" not in task_ids
    assert "task2" in task_ids
    assert "task3" in task_ids
    assert len(data.tasks) == 2


def test_delete_completed_tasks_includes_failed(client, test_tasks):
    # Setup data with a failed task
    with tasks.lock_tasks(test_tasks):
        data = tasks.load_tasks(test_tasks)
        data.tasks.append(
            tasks.Task(
                id="failed_task",
                description="Failed Task",
                status=tasks.TaskStatus.FAILED,
                attempts=1,
                outcomes=["Error"],
                completed_at=123456789.0,
            )
        )
        tasks.save_tasks(test_tasks, data)

    response = client.post("/api/tasks/delete-completed")
    assert response.status_code == 200

    # Verify tasks in file
    data = tasks.load_tasks(test_tasks)
    task_ids = [t.id for t in data.tasks]
    assert "task1" not in task_ids  # task1 is completed
    assert "failed_task" not in task_ids  # failed_task should be deleted
    assert "task2" in task_ids
    assert "task3" in task_ids
    assert len(data.tasks) == 2


def test_delete_specific_task(client, test_tasks):
    response = client.post("/api/tasks/task2/delete")
    assert response.status_code == 200

    data = tasks.load_tasks(test_tasks)
    task_ids = [t.id for t in data.tasks]
    assert "task2" not in task_ids
    assert len(data.tasks) == 2


def test_update_task_description(client, test_tasks):
    response = client.post(
        "/api/tasks/task2/update", json={"description": "Updated Pending Task"}
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Updated Pending Task"

    data = tasks.load_tasks(test_tasks)
    task2 = next(t for t in data.tasks if t.id == "task2")
    assert task2.description == "Updated Pending Task"


def test_update_completed_task_description_fails(client, test_tasks):
    response = client.post(
        "/api/tasks/task1/update",
        json={"description": "Attempt to update completed task"},
    )
    assert response.status_code == 400
    assert "Cannot edit description of a completed task" in response.json()["detail"]


def test_mark_task_failed_via_api(client, test_tasks):
    # task2 is pending with no outcomes
    # 1. Try to fail without outcomes -> should fail
    response = client.post(
        "/api/tasks/task2/update", json={"status": tasks.TaskStatus.FAILED}
    )
    assert response.status_code == 400
    assert "has no recorded outcomes" in response.json()["detail"]

    # 2. Add outcome and try again
    with tasks.lock_tasks(test_tasks):
        data = tasks.load_tasks(test_tasks)
        task2 = next(t for t in data.tasks if t.id == "task2")
        task2.outcomes = ["Failed attempt"]
        tasks.save_tasks(test_tasks, data)

    response = client.post(
        "/api/tasks/task2/update", json={"status": tasks.TaskStatus.FAILED}
    )
    assert response.status_code == 200
    assert response.json()["status"] == tasks.TaskStatus.FAILED

    data = tasks.load_tasks(test_tasks)
    task2 = next(t for t in data.tasks if t.id == "task2")
    assert task2.status == tasks.TaskStatus.FAILED


def test_uncomplete_task_via_api(client, test_tasks):
    # Updating status of a completed task should still be allowed
    response = client.post(
        "/api/tasks/task1/update", json={"status": tasks.TaskStatus.PENDING}
    )
    assert response.status_code == 200
    assert response.json()["status"] == tasks.TaskStatus.PENDING
    assert response.json()["attempts"] == 0

    data = tasks.load_tasks(test_tasks)
    task1 = next(t for t in data.tasks if t.id == "task1")
    assert task1.status == tasks.TaskStatus.PENDING
    assert task1.attempts == 0


def test_reopen_cancelled_task_via_api(client, test_tasks):
    # First cancel task2 (pending), then reopen it
    response = client.post(
        "/api/tasks/task2/update", json={"status": tasks.TaskStatus.CANCELLED}
    )
    assert response.status_code == 200
    assert response.json()["status"] == tasks.TaskStatus.CANCELLED

    # Now reopen the cancelled task back to pending
    response = client.post(
        "/api/tasks/task2/update", json={"status": tasks.TaskStatus.PENDING}
    )
    assert response.status_code == 200
    assert response.json()["status"] == tasks.TaskStatus.PENDING
    assert response.json()["attempts"] == 0


def test_has_log_population(client, test_tasks):
    # Initially no logs
    response = client.get("/api/data")
    assert response.status_code == 200
    data = response.json()
    for task in data["tasks"]:
        assert task["has_runner_log"] is False

    # Create a runner log for task1
    log_file = paths.get_log_file(api.app.state.tasks_file, "task1")
    log_file.write_text("Some logs")

    response = client.get("/api/data")
    assert response.status_code == 200
    data = response.json()
    task1 = next(t for t in data["tasks"] if t["id"] == "task1")
    assert task1["has_runner_log"] is True

    task2 = next(t for t in data["tasks"] if t["id"] == "task2")
    assert task2["has_runner_log"] is False


def test_get_single_task(client, test_tasks):
    # task1 exists in test_tasks fixture
    resp = client.get("/api/tasks/task1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "task1"
    assert resp.json()["description"] == "Completed Task"


def test_get_nonexistent_task(client, test_tasks):
    resp = client.get("/api/tasks/nonexistent")
    assert resp.status_code == 404


def test_api_log(client, test_tasks):
    test_tasks_file = test_tasks
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


def test_api_delete_log_cleanup(client, test_tasks):
    test_tasks_file = test_tasks
    # 1. Add a task
    data = tasks.load_tasks(test_tasks_file)
    task_id = "api_delete_test"
    data.tasks.append(
        tasks.Task(
            id=task_id,
            description="api delete test",
            status=tasks.TaskStatus.PENDING,
            attempts=0,
            outcomes=[],
        )
    )
    tasks.save_tasks(test_tasks_file, data)

    # 2. Create log manually
    log_file = paths.get_log_file(test_tasks_file, task_id)
    log_file.write_text("API delete log")
    assert log_file.exists()

    # 3. Delete via API
    response = client.post(f"/api/tasks/{task_id}/delete")
    assert response.status_code == 200
    assert not log_file.exists()


def test_project_param_get_data(client, test_tasks):
    """GET /api/data?project=subdir returns data for that project."""
    root = api.app.state.root
    subdir = root / "myproject"
    subdir.mkdir(exist_ok=True)

    # Add a task via the project param
    response = client.post(
        "/api/tasks",
        json={"description": "Sub-project task"},
        params={"project": "myproject"},
    )
    assert response.status_code == 200

    # Fetch data for the sub-project
    response = client.get("/api/data", params={"project": "myproject"})
    assert response.status_code == 200
    data = response.json()
    assert any(t["description"] == "Sub-project task" for t in data["tasks"])

    # Default project should not have this task
    response = client.get("/api/data")
    assert response.status_code == 200
    data = response.json()
    assert not any(t["description"] == "Sub-project task" for t in data["tasks"])


def test_project_delete_completed_isolation(client, test_tasks):
    """Deleting completed tasks in root does not affect sub-projects."""
    root = api.app.state.root
    subdir = root / "isolated"
    subdir.mkdir(exist_ok=True)

    # Add and complete a task in the sub-project
    res = client.post(
        "/api/tasks",
        json={"description": "Sub task"},
        params={"project": "isolated"},
    )
    assert res.status_code == 200
    task_id = res.json()["id"]
    # Mark it completed (requires outcomes, so use update_task directly)
    tasks.update_task(
        paths.get_tasks_file_for_dir(subdir),
        task_id,
        status=tasks.TaskStatus.COMPLETED,
        require_outcomes=False,
    )

    # Delete completed in ROOT
    response = client.post("/api/tasks/delete-completed")
    assert response.status_code == 200

    # Sub-project's completed task should still exist
    res = client.get("/api/data", params={"project": "isolated"})
    assert res.status_code == 200
    assert any(t["id"] == task_id for t in res.json()["tasks"])


def test_add_task_auto_starts_loop(client, test_tasks):
    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return False
        with patch("lemming.tasks.is_loop_running", return_value=False):
            # Set auto-start to True for this test
            api.app.state.disable_auto_start = False
            try:
                response = client.post("/api/tasks", json={"description": "New task"})
                assert response.status_code == 200

                # Verify Popen was called to start the loop
                mock_popen.assert_called_once()
                args, kwargs = mock_popen.call_args
                cmd = args[0]
                assert "run" in cmd
                assert str(test_tasks) in cmd
            finally:
                api.app.state.disable_auto_start = True


def test_add_task_does_not_restart_if_running(client, test_tasks):
    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return True
        with patch("lemming.tasks.is_loop_running", return_value=True):
            response = client.post("/api/tasks", json={"description": "New task"})
            assert response.status_code == 200

            # Verify Popen was NOT called
            mock_popen.assert_not_called()


def test_update_task_to_pending_does_not_start_loop(client, test_tasks):
    # Add a completed task
    task = tasks.add_task(test_tasks, "Completed task")
    tasks.add_outcome(test_tasks, task.id, "Done")
    tasks.update_task(test_tasks, task.id, status=tasks.TaskStatus.COMPLETED)

    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return False
        with patch("lemming.tasks.is_loop_running", return_value=False):
            # Update to pending
            response = client.post(
                f"/api/tasks/{task.id}/update",
                json={"status": tasks.TaskStatus.PENDING},
            )
            assert response.status_code == 200

            # Verify Popen was NOT called
            mock_popen.assert_not_called()


def test_clear_task_does_not_start_loop(client, test_tasks):
    # Add a completed task
    task = tasks.add_task(test_tasks, "Completed task")
    tasks.add_outcome(test_tasks, task.id, "Done")
    tasks.update_task(test_tasks, task.id, status=tasks.TaskStatus.COMPLETED)

    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return False
        with patch("lemming.tasks.is_loop_running", return_value=False):
            # Clear task
            response = client.post(f"/api/tasks/{task.id}/clear")
            assert response.status_code == 200

            # Verify Popen was NOT called
            mock_popen.assert_not_called()


def test_add_task_respects_disable_auto_start(client, test_tasks):
    api.app.state.disable_auto_start = True
    with patch("subprocess.Popen") as mock_popen:
        with patch("lemming.tasks.is_loop_running", return_value=False):
            response = client.post(
                "/api/tasks", json={"description": "No auto-start task"}
            )
            assert response.status_code == 200

            # Verify Popen was NOT called
            mock_popen.assert_not_called()


def test_add_task_starts_loop_with_cwd(test_workspace, client):
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


def test_cancel_task_endpoint(client, test_tasks):
    with patch("lemming.tasks.cancel_task", return_value=True):
        response = client.post("/api/tasks/task3/cancel")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    with patch("lemming.tasks.cancel_task", return_value=False):
        response = client.post("/api/tasks/nonexistent/cancel")
        assert response.status_code == 404
