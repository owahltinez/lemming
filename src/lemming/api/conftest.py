import os
import pathlib
import shutil
import subprocess
import tempfile
import time

import pytest
import fastapi.testclient

from lemming import api
from lemming import paths
from lemming import tasks


@pytest.fixture
def client():
    return fastapi.testclient.TestClient(api.app)


@pytest.fixture
def test_tasks():
    # Create a temporary directory and a tasks file
    test_dir = tempfile.mkdtemp()
    test_tasks_file = pathlib.Path(test_dir) / "tasks_test.yml"

    # Scaffold a valid file
    data = tasks.Roadmap(
        context="Initial context",
        tasks=[
            tasks.Task(
                id="task1",
                description="Completed Task",
                status=tasks.TaskStatus.COMPLETED,
                attempts=1,
                outcomes=["All good"],
                completed_at=123456789.0,
            ),
            tasks.Task(
                id="task2",
                description="Pending Task",
                status=tasks.TaskStatus.PENDING,
                attempts=0,
                outcomes=[],
            ),
            tasks.Task(
                id="task3",
                description="In Progress Task",
                status=tasks.TaskStatus.IN_PROGRESS,
                attempts=1,
                outcomes=[],
                pid=os.getpid(),
                last_heartbeat=time.time(),
            ),
        ],
    )
    tasks.save_tasks(test_tasks_file, data)

    # Override the TASKS_FILE and root in the api module
    original_tasks_file = api.app.state.tasks_file
    original_root = api.app.state.root
    original_auto_start = api.app.state.disable_auto_start
    api.app.state.tasks_file = test_tasks_file
    api.app.state.root = pathlib.Path(test_dir).resolve()
    api.app.state.disable_auto_start = True

    yield test_tasks_file

    # Restore the originals
    api.app.state.tasks_file = original_tasks_file
    api.app.state.root = original_root
    api.app.state.disable_auto_start = original_auto_start
    shutil.rmtree(test_dir)


@pytest.fixture
def git_repo():
    # Create a temporary directory and initialize a git repo
    test_dir = tempfile.mkdtemp()
    orig_cwd = os.getcwd()
    original_root = api.app.state.root
    os.chdir(test_dir)
    api.app.state.root = pathlib.Path(test_dir).resolve()

    # Clear cached git repo check from previous tests
    if hasattr(paths.in_git_repo, "_result"):
        del paths.in_git_repo._result

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
    if hasattr(paths.in_git_repo, "_result"):
        del paths.in_git_repo._result
    os.chdir(orig_cwd)
    api.app.state.root = original_root
    shutil.rmtree(test_dir)


@pytest.fixture
def non_git_dir():
    """A temporary directory that is NOT a git repo."""
    test_dir = tempfile.mkdtemp()
    orig_cwd = os.getcwd()
    original_root = api.app.state.root
    os.chdir(test_dir)
    api.app.state.root = pathlib.Path(test_dir).resolve()

    # Clear cached git repo check
    if hasattr(paths.in_git_repo, "_result"):
        del paths.in_git_repo._result

    # Create files (including one that would be "ignored" if git were present)
    (pathlib.Path(test_dir) / "file1.txt").write_text("content1")
    (pathlib.Path(test_dir) / "ignored.txt").write_text("not actually ignored")

    yield pathlib.Path(test_dir)

    if hasattr(paths.in_git_repo, "_result"):
        del paths.in_git_repo._result
    os.chdir(orig_cwd)
    api.app.state.root = original_root
    shutil.rmtree(test_dir)


@pytest.fixture
def temp_repo(tmp_path, monkeypatch):
    """Create a temporary directory and set it as the API root."""
    root = tmp_path / "repo"
    root.mkdir()

    # Mock api.app.state.root
    original_root = api.app.state.root
    api.app.state.root = root

    yield root

    # Restore original root
    api.app.state.root = original_root


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
            tasks.Task(
                id="sub1", description="Sub Task 1", status=tasks.TaskStatus.PENDING
            ),
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
