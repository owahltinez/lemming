from unittest.mock import patch

from lemming import api


def test_run_loop(client, test_tasks):
    with patch("subprocess.Popen") as mock_popen:
        response = client.post("/api/run", json={"env": {"KEY": "VALUE"}})
        assert response.status_code == 200
        assert response.json() == {"status": "started"}

        args = mock_popen.call_args[0][0]
        assert "run" in args
        # Check that we didn't pass redundant flags
        assert "--runner" not in args
        assert "--retries" not in args
        assert "--hook" not in args


def test_get_runners(client):
    response = client.get("/api/runners")
    assert response.status_code == 200
    assert response.json() == ["agy", "aider", "claude", "codex"]


def test_project_goal_isolation(client, test_tasks):
    """The goal is isolated per project."""
    root = api.app.state.root
    subdir = root / "goal_test"
    subdir.mkdir(exist_ok=True)

    # Set the goal for the sub-project
    response = client.post(
        "/api/goal",
        json={"goal": "Sub-project goal"},
        params={"project": "goal_test"},
    )
    assert response.status_code == 200

    # Root goal should be unchanged
    res = client.get("/api/data")
    assert res.status_code == 200
    assert res.json()["goal"] == "Initial goal"

    # Sub-project goal should be set
    res = client.get("/api/data", params={"project": "goal_test"})
    assert res.status_code == 200
    assert res.json()["goal"] == "Sub-project goal"


def test_run_loop_with_project(client, test_tasks):
    """POST /api/run with project param uses the correct tasks file."""
    root = api.app.state.root
    subdir = root / "run_project"
    subdir.mkdir(exist_ok=True)
    (subdir / "tasks.yml").touch()

    with patch("subprocess.Popen") as mock_popen:
        response = client.post(
            "/api/run",
            json={"runner": "claude"},
            params={"project": "run_project"},
        )
        assert response.status_code == 200
        args = mock_popen.call_args[0][0]
        # The tasks file should be for the sub-project, not the default
        tasks_file_idx = args.index("--tasks-file") + 1
        assert "run_project" in args[tasks_file_idx]


def test_run_loop_with_project_cwd(test_workspace, client):
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
