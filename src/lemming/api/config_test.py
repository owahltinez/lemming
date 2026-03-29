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
    assert response.json() == ["gemini", "aider", "claude", "codex"]


def test_project_context_isolation(client, test_tasks):
    """Context is isolated per project."""
    root = api.app.state.root
    subdir = root / "ctx_test"
    subdir.mkdir(exist_ok=True)

    # Set context for sub-project
    response = client.post(
        "/api/context",
        json={"context": "Sub-project context"},
        params={"project": "ctx_test"},
    )
    assert response.status_code == 200

    # Root context should be unchanged
    res = client.get("/api/data")
    assert res.status_code == 200
    assert res.json()["context"] == "Initial context"

    # Sub-project context should be set
    res = client.get("/api/data", params={"project": "ctx_test"})
    assert res.status_code == 200
    assert res.json()["context"] == "Sub-project context"


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
