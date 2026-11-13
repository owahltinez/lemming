from unittest.mock import patch

from lemming import models, paths, persistence, snapshots
from lemming.api import main
from lemming.tasks import limits, operations, progress


def test_get_data(client, test_tasks):
    response = client.get("/api/data")
    assert response.status_code == 200
    data = response.json()
    assert data["goal"] == "Initial goal"
    assert len(data["tasks"]) == 3
    # Check that task1 is in the list
    task1 = next(t for t in data["tasks"] if t["id"] == "task1")
    assert task1["status"] == models.TaskStatus.COMPLETED
    assert data["loop_running"] is True


def test_add_task(client, test_tasks):
    response = client.post(
        "/api/tasks", json={"description": "New task from test"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "New task from test"
    assert data["id"]  # id should be auto-generated
    assert data["status"] == models.TaskStatus.PENDING

    # Verify task was persisted
    roadmap = persistence.load_tasks(test_tasks)
    assert any(t.description == "New task from test" for t in roadmap.tasks)


def test_add_task_rejects_oversized_description(client, test_tasks):
    response = client.post(
        "/api/tasks",
        json={
            "description": "x" * (limits.MAX_TASK_DESCRIPTION_CHARS + 1),
        },
    )

    assert response.status_code == 400
    assert "2,001 characters (limit 2,000)" in response.json()["detail"]
    assert len(persistence.load_tasks(test_tasks).tasks) == 3


def test_add_task_with_runner(client, test_tasks):
    response = client.post(
        "/api/tasks", json={"description": "Runner task", "runner": "claude"}
    )
    assert response.status_code == 200
    assert response.json()["runner"] == "claude"


def test_add_task_with_hooks(client, test_tasks):
    response = client.post(
        "/api/tasks",
        json={"description": "Reviewed", "hooks": ["ux", "readability"]},
    )
    assert response.status_code == 200
    assert response.json()["hooks"] == ["readability", "ux"]


def test_add_task_rejects_invalid_hooks(client, test_tasks):
    for hooks, message in (
        (["nope"], "Unknown review 'nope'"),
        (["roadmap"], "orchestrates the roadmap"),
    ):
        response = client.post(
            "/api/tasks", json={"description": "Bad", "hooks": hooks}
        )
        assert response.status_code == 400
        assert message in response.json()["detail"]
    assert len(persistence.load_tasks(test_tasks).tasks) == 3


def test_update_task_hooks_and_reset(client, test_tasks):
    url = "/api/tasks/task2/update"

    response = client.post(url, json={"hooks": []})
    assert response.status_code == 200
    assert response.json()["hooks"] == []

    response = client.post(url, json={"description": "Still custom"})
    assert response.json()["hooks"] == []

    response = client.post(url, json={"hooks": None})
    assert response.status_code == 200
    assert response.json()["hooks"] is None


def test_update_task_rejects_invalid_hooks(client, test_tasks):
    for hooks in (["nope"], ["roadmap"], "ux", [1]):
        response = client.post("/api/tasks/task2/update", json={"hooks": hooks})
        assert response.status_code == 400, hooks
    task2 = next(
        t for t in persistence.load_tasks(test_tasks).tasks if t.id == "task2"
    )
    assert task2.hooks is None


def test_delete_completed_tasks(client, test_tasks):
    response = client.post("/api/tasks/delete-completed")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # Verify tasks in file
    data = persistence.load_tasks(test_tasks)
    task_ids = [t.id for t in data.tasks]
    assert "task1" not in task_ids
    assert "task2" in task_ids
    assert "task3" in task_ids
    assert len(data.tasks) == 2


def test_delete_completed_tasks_includes_failed(client, test_tasks):
    # Setup data with a failed task
    with persistence.lock_tasks(test_tasks):
        data = persistence.load_tasks(test_tasks)
        data.tasks.append(
            models.Task(
                id="failed_task",
                description="Failed Task",
                status=models.TaskStatus.FAILED,
                attempts=1,
                progress=["Error"],
                completed_at=123456789.0,
            )
        )
        persistence.save_tasks(test_tasks, data)

    response = client.post("/api/tasks/delete-completed")
    assert response.status_code == 200

    # Verify tasks in file
    data = persistence.load_tasks(test_tasks)
    task_ids = [t.id for t in data.tasks]
    assert "task1" not in task_ids  # task1 is completed
    assert "failed_task" not in task_ids  # failed_task should be deleted
    assert "task2" in task_ids
    assert "task3" in task_ids
    assert len(data.tasks) == 2


def test_delete_specific_task(client, test_tasks):
    response = client.post("/api/tasks/task2/delete")
    assert response.status_code == 200

    data = persistence.load_tasks(test_tasks)
    task_ids = [t.id for t in data.tasks]
    assert "task2" not in task_ids
    assert len(data.tasks) == 2


def test_update_task_description(client, test_tasks):
    response = client.post(
        "/api/tasks/task2/update", json={"description": "Updated Pending Task"}
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Updated Pending Task"

    data = persistence.load_tasks(test_tasks)
    task2 = next(t for t in data.tasks if t.id == "task2")
    assert task2.description == "Updated Pending Task"


def test_update_completed_task_description_fails(client, test_tasks):
    response = client.post(
        "/api/tasks/task1/update",
        json={"description": "Attempt to update completed task"},
    )
    assert response.status_code == 400
    assert (
        "Cannot edit description of a completed task"
        in response.json()["detail"]
    )


def test_mark_task_failed_via_api(client, test_tasks):
    # task2 is pending with no progress
    # 1. Try to fail without progress -> should fail
    response = client.post(
        "/api/tasks/task2/update", json={"status": models.TaskStatus.FAILED}
    )
    assert response.status_code == 400
    assert "has no recorded progress" in response.json()["detail"]

    # 2. Add progress and try again
    with persistence.lock_tasks(test_tasks):
        data = persistence.load_tasks(test_tasks)
        task2 = next(t for t in data.tasks if t.id == "task2")
        task2.progress = ["Failed attempt"]
        persistence.save_tasks(test_tasks, data)

    response = client.post(
        "/api/tasks/task2/update", json={"status": models.TaskStatus.FAILED}
    )
    assert response.status_code == 200
    assert response.json()["status"] == models.TaskStatus.FAILED

    data = persistence.load_tasks(test_tasks)
    task2 = next(t for t in data.tasks if t.id == "task2")
    assert task2.status == models.TaskStatus.FAILED


def test_uncomplete_task_via_api(client, test_tasks):
    # Updating status of a completed task should still be allowed
    response = client.post(
        "/api/tasks/task1/update", json={"status": models.TaskStatus.PENDING}
    )
    assert response.status_code == 200
    assert response.json()["status"] == models.TaskStatus.PENDING
    assert response.json()["attempts"] == 0

    data = persistence.load_tasks(test_tasks)
    task1 = next(t for t in data.tasks if t.id == "task1")
    assert task1.status == models.TaskStatus.PENDING
    assert task1.attempts == 0


def test_reopen_cancelled_task_via_api(client, test_tasks):
    # First cancel task2 (pending), then reopen it
    response = client.post(
        "/api/tasks/task2/update", json={"status": models.TaskStatus.CANCELLED}
    )
    assert response.status_code == 200
    assert response.json()["status"] == models.TaskStatus.CANCELLED

    # Now reopen the cancelled task back to pending
    response = client.post(
        "/api/tasks/task2/update", json={"status": models.TaskStatus.PENDING}
    )
    assert response.status_code == 200
    assert response.json()["status"] == models.TaskStatus.PENDING
    assert response.json()["attempts"] == 0


def test_supersede_and_reopen_task_via_api(client, test_tasks):
    response = client.post(
        "/api/tasks/task2/supersede",
        json={"reason": "split after timeout"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == models.TaskStatus.SUPERSEDED
    assert response.json()["superseded_reason"] == "split after timeout"

    response = client.post(
        "/api/tasks/task2/update",
        json={"status": models.TaskStatus.PENDING},
    )
    assert response.status_code == 200
    assert response.json()["status"] == models.TaskStatus.PENDING
    assert response.json()["superseded_reason"] is None
    assert response.json()["superseded_at"] is None


def test_has_log_population(client, test_tasks):
    # Initially no logs
    response = client.get("/api/data")
    assert response.status_code == 200
    data = response.json()
    for task in data["tasks"]:
        assert task["has_runner_log"] is False

    # Create a runner log for task1
    log_file = paths.get_log_file(main.app.state.tasks_file, "task1")
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


def test_api_log_serves_non_utf8_argv(client, test_tasks):
    log_file = paths.get_log_file(test_tasks, "testlogid")
    log_file.write_bytes(b"Command: true caf\xe9\n")

    response = client.get("/api/tasks/testlogid/log")
    assert response.status_code == 200
    assert response.json() == {"log": "Command: true caf\udce9\n"}


def test_api_delete_retains_log(client, test_tasks):
    test_tasks_file = test_tasks
    # 1. Add a task
    data = persistence.load_tasks(test_tasks_file)
    task_id = "api_delete_test"
    data.tasks.append(
        models.Task(
            id=task_id,
            description="api delete test",
            status=models.TaskStatus.PENDING,
            attempts=0,
            progress=[],
        )
    )
    persistence.save_tasks(test_tasks_file, data)

    # 2. Create log manually
    log_file = paths.get_log_file(test_tasks_file, task_id)
    log_file.write_text("API delete log")
    assert log_file.exists()

    # 3. Delete via API
    response = client.post(f"/api/tasks/{task_id}/delete")
    assert response.status_code == 200
    assert log_file.read_text() == "API delete log"

    response = client.get(f"/api/tasks/{task_id}/log")
    assert response.status_code == 200
    assert response.json() == {"log": "API delete log"}


def test_api_delete_completed_cleans_up_log(client, test_tasks):
    log_file = paths.get_log_file(test_tasks, "task1")
    log_file.write_text("completed task log")

    response = client.post("/api/tasks/delete-completed")

    assert response.status_code == 200
    assert not log_file.exists()


def test_project_param_get_data(client, test_tasks):
    """GET /api/data?project=subdir returns data for that project."""
    root = main.app.state.root
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
    assert not any(
        t["description"] == "Sub-project task" for t in data["tasks"]
    )


def test_project_delete_completed_isolation(client, test_tasks):
    """Deleting completed tasks in root does not affect sub-projects."""
    root = main.app.state.root
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
    # Mark it completed (requires progress, so use update_task directly)
    operations.update_task(
        paths.get_tasks_file_for_dir(subdir),
        task_id,
        status=models.TaskStatus.COMPLETED,
        require_progress=False,
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
        with patch(
            "lemming.tasks.lifecycle.is_loop_running", return_value=False
        ):
            # Set auto-start to True for this test
            main.app.state.disable_auto_start = False
            try:
                response = client.post(
                    "/api/tasks", json={"description": "New task"}
                )
                assert response.status_code == 200

                # Verify Popen was called to start the loop
                mock_popen.assert_called_once()
                args, kwargs = mock_popen.call_args
                cmd = args[0]
                assert "run" in cmd
                assert str(test_tasks) in cmd
            finally:
                main.app.state.disable_auto_start = True


def test_add_task_does_not_restart_if_running(client, test_tasks):
    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return True
        with patch(
            "lemming.tasks.lifecycle.is_loop_running", return_value=True
        ):
            response = client.post(
                "/api/tasks", json={"description": "New task"}
            )
            assert response.status_code == 200

            # Verify Popen was NOT called
            mock_popen.assert_not_called()


def test_update_task_to_pending_does_not_start_loop(client, test_tasks):
    # Add a completed task
    task = operations.add_task(test_tasks, "Completed task")
    progress.add_progress(test_tasks, task.id, "Done")
    operations.update_task(
        test_tasks, task.id, status=models.TaskStatus.COMPLETED
    )

    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return False
        with patch(
            "lemming.tasks.lifecycle.is_loop_running", return_value=False
        ):
            # Update to pending
            response = client.post(
                f"/api/tasks/{task.id}/update",
                json={"status": models.TaskStatus.PENDING},
            )
            assert response.status_code == 200

            # Verify Popen was NOT called
            mock_popen.assert_not_called()


def test_clear_task_does_not_start_loop(client, test_tasks):
    # Add a completed task
    task = operations.add_task(test_tasks, "Completed task")
    progress.add_progress(test_tasks, task.id, "Done")
    operations.update_task(
        test_tasks, task.id, status=models.TaskStatus.COMPLETED
    )

    with patch("subprocess.Popen") as mock_popen:
        # Mock is_loop_running to return False
        with patch(
            "lemming.tasks.lifecycle.is_loop_running", return_value=False
        ):
            # Clear task
            response = client.post(f"/api/tasks/{task.id}/clear")
            assert response.status_code == 200

            # Verify Popen was NOT called
            mock_popen.assert_not_called()


def test_add_task_respects_disable_auto_start(client, test_tasks):
    main.app.state.disable_auto_start = True
    with patch("subprocess.Popen") as mock_popen:
        with patch(
            "lemming.tasks.lifecycle.is_loop_running", return_value=False
        ):
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
    with patch(
        "lemming.tasks.lifecycle.cancel_task", return_value=True
    ) as mock_cancel:
        response = client.post("/api/tasks/task3/cancel")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        mock_cancel.assert_called_once_with(
            test_tasks,
            "task3",
            working_dir=main.app.state.root,
        )

    with patch("lemming.tasks.lifecycle.cancel_task", return_value=False):
        response = client.post("/api/tasks/nonexistent/cancel")
        assert response.status_code == 404


def test_get_data_reports_corrupted_tasks_file(client, test_tasks):
    corrupt = "tasks: [unclosed\n"
    test_tasks.write_text(corrupt, encoding="utf-8")

    response = client.get("/api/data")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert str(test_tasks) in detail
    assert f"{test_tasks}.corrupt" in detail
    # The route must not rewrite the file it could not read.
    assert test_tasks.read_text(encoding="utf-8") == corrupt


def test_add_task_refuses_corrupted_tasks_file(client, test_tasks):
    corrupt = "tasks: [unclosed\n"
    test_tasks.write_text(corrupt, encoding="utf-8")

    response = client.post("/api/tasks", json={"description": "New task"})

    assert response.status_code == 500
    assert str(test_tasks) in response.json()["detail"]
    assert test_tasks.read_text(encoding="utf-8") == corrupt


def test_add_task_rejects_outside_parent_tasks_file(client, test_tasks):
    for bad_path in ("/etc/passwd", "../../outside.yml"):
        response = client.post(
            "/api/tasks",
            json={
                "description": "Child task",
                "parent": "task1",
                "parent_tasks_file": bad_path,
            },
        )
        assert response.status_code == 400, bad_path


def test_get_task_log_rejects_traversal(client, test_tasks):
    response = client.get("/api/tasks/..%2Fsecret/log")
    assert response.status_code in (400, 404)


def test_get_task_diff_and_rollback_endpoints(client, test_tasks, monkeypatch):
    root = main.app.state.root
    monkeypatch.setenv("LEMMING_HOME", str(root / "lemming_home"))
    sample = root / "sample.py"
    sample.write_text("before\n", encoding="utf-8")
    pre_tree = snapshots.snapshot_tree(test_tasks, root)
    assert pre_tree is not None

    sample.write_text("after\n", encoding="utf-8")
    runner_tree = snapshots.snapshot_tree(test_tasks, root)
    assert runner_tree is not None

    sample.write_text("after\n# formatted\n", encoding="utf-8")
    post_tree = snapshots.snapshot_tree(test_tasks, root)
    assert post_tree is not None
    summary = snapshots.summarize_diff(test_tasks, pre_tree, post_tree)

    with persistence.lock_tasks(test_tasks):
        data = persistence.load_tasks(test_tasks)
        task1 = next(t for t in data.tasks if t.id == "task1")
        task1.pre_tree = pre_tree
        task1.runner_tree = runner_tree
        task1.post_tree = post_tree
        task1.diff_summary = summary
        task1.hook_trees = {"readability": [runner_tree, post_tree]}
        persistence.save_tasks(test_tasks, data)

    diff_resp = client.get("/api/tasks/task1/diff")
    assert diff_resp.status_code == 200
    payload = diff_resp.json()
    assert "-before" in payload["diff"]
    assert "+after" in payload["diff"]
    assert payload["summary"] == summary

    hook_diff_resp = client.get(
        "/api/tasks/task1/diff", params={"hook": "readability"}
    )
    assert hook_diff_resp.status_code == 200
    assert "+# formatted" in hook_diff_resp.json()["diff"]
    assert (
        client.get(
            "/api/tasks/task1/diff", params={"hook": "missing"}
        ).status_code
        == 404
    )

    assert client.get("/api/tasks/nonexistent/diff").status_code == 404

    with patch("lemming.tasks.lifecycle.is_loop_running", return_value=True):
        busy = client.post("/api/tasks/task1/rollback", json={"only": True})
        assert busy.status_code == 400

    with patch("lemming.tasks.lifecycle.is_loop_running", return_value=False):
        active_task_busy = client.post(
            "/api/tasks/task1/rollback", json={"only": True}
        )
        assert active_task_busy.status_code == 400

        with persistence.lock_tasks(test_tasks):
            data = persistence.load_tasks(test_tasks)
            task3 = next(t for t in data.tasks if t.id == "task3")
            task3.status = models.TaskStatus.PENDING
            task3.pid = None
            task3.last_heartbeat = None
            persistence.save_tasks(test_tasks, data)

        combo = client.post(
            "/api/tasks/task1/rollback",
            json={"only": True, "hook": "readability"},
        )
        assert combo.status_code == 400

        hook_rb = client.post(
            "/api/tasks/task1/rollback", json={"hook": "readability"}
        )
        assert hook_rb.status_code == 200
        assert sample.read_text(encoding="utf-8") == "after\n"
        task1_mid = next(
            t
            for t in persistence.load_tasks(test_tasks).tasks
            if t.id == "task1"
        )
        assert task1_mid.status == models.TaskStatus.COMPLETED
        assert task1_mid.hook_trees is None

        rb_resp = client.post("/api/tasks/task1/rollback", json={"only": True})
        assert rb_resp.status_code == 200
        assert sample.read_text(encoding="utf-8") == "before\n"
        task1_after = next(
            t
            for t in persistence.load_tasks(test_tasks).tasks
            if t.id == "task1"
        )
        assert task1_after.status == models.TaskStatus.PENDING
        assert task1_after.pre_tree is None

        sample.write_text("unrelated dirty edit\n", encoding="utf-8")
        with persistence.lock_tasks(test_tasks):
            data = persistence.load_tasks(test_tasks)
            task1 = next(t for t in data.tasks if t.id == "task1")
            task1.status = models.TaskStatus.CANCELLED
            task1.pre_tree = pre_tree
            task1.runner_tree = None
            task1.post_tree = None
            persistence.save_tasks(test_tasks, data)

        cancelled_diff = client.get("/api/tasks/task1/diff")
        assert cancelled_diff.status_code == 200
        assert cancelled_diff.json() == {"diff": "", "summary": None}


def test_get_task_diff_serves_non_utf8_content(client, test_tasks, monkeypatch):
    root = main.app.state.root
    monkeypatch.setenv("LEMMING_HOME", str(root / "lemming_home"))
    pre_tree = snapshots.snapshot_tree(test_tasks, root)
    (root / "notes.txt").write_bytes(b"caf\xe9\n")
    post_tree = snapshots.snapshot_tree(test_tasks, root)

    with persistence.lock_tasks(test_tasks):
        data = persistence.load_tasks(test_tasks)
        data.tasks[0].pre_tree = pre_tree
        data.tasks[0].post_tree = post_tree
        persistence.save_tasks(test_tasks, data)

    response = client.get("/api/tasks/task1/diff")
    assert response.status_code == 200
    assert "+caf\udce9" in response.json()["diff"]


def test_task_fields_with_surrogates_are_served(client, test_tasks):
    entry = "saw bad\udcff.txt"
    with persistence.lock_tasks(test_tasks):
        data = persistence.load_tasks(test_tasks)
        data.tasks[0].progress = [entry]
        persistence.save_tasks(test_tasks, data)

    data_resp = client.get("/api/data")
    assert data_resp.status_code == 200
    task1 = next(t for t in data_resp.json()["tasks"] if t["id"] == "task1")
    assert task1["progress"] == [entry]

    task_resp = client.get("/api/tasks/task1")
    assert task_resp.status_code == 200
    assert task_resp.json()["progress"] == [entry]


def test_rollback_error_with_surrogate_keeps_its_status(client, test_tasks):
    with persistence.lock_tasks(test_tasks):
        data = persistence.load_tasks(test_tasks)
        data.tasks[2].status = models.TaskStatus.PENDING
        data.tasks[2].pid = None
        persistence.save_tasks(test_tasks, data)

    # Raw body: a JSON escape is the only way to send a lone surrogate.
    response = client.post(
        "/api/tasks/task1/rollback",
        content=b'{"hook": "bad\\udcff"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert "bad\udcff" in response.json()["detail"]
