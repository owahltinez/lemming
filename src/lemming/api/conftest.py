"""Shared pytest fixtures for the API test suite."""

import os
import pathlib
import shutil
import subprocess
import tempfile
import time

import fastapi.testclient
import pytest

from lemming import models, paths, persistence
from lemming.api import main


@pytest.fixture
def client():
    """A TestClient bound to the lemming FastAPI app."""
    return fastapi.testclient.TestClient(main.app)


@pytest.fixture
def test_tasks():
    """A temporary tasks file with sample tasks, wired into the app state."""
    # Create a temporary directory and a tasks file
    test_dir = tempfile.mkdtemp()
    test_tasks_file = pathlib.Path(test_dir) / "tasks_test.yml"

    # Scaffold a valid file
    data = models.Roadmap(
        goal="Initial goal",
        tasks=[
            models.Task(
                id="task1",
                description="Completed Task",
                status=models.TaskStatus.COMPLETED,
                attempts=1,
                progress=["All good"],
                completed_at=123456789.0,
            ),
            models.Task(
                id="task2",
                description="Pending Task",
                status=models.TaskStatus.PENDING,
                attempts=0,
                progress=[],
            ),
            models.Task(
                id="task3",
                description="In Progress Task",
                status=models.TaskStatus.IN_PROGRESS,
                attempts=1,
                progress=[],
                pid=os.getpid(),
                last_heartbeat=time.time(),
            ),
        ],
    )
    persistence.save_tasks(test_tasks_file, data)

    # Override the TASKS_FILE and root in the api module
    original_tasks_file = main.app.state.tasks_file
    original_root = main.app.state.root
    original_auto_start = main.app.state.disable_auto_start
    main.app.state.tasks_file = test_tasks_file
    main.app.state.root = pathlib.Path(test_dir).resolve()
    main.app.state.disable_auto_start = True

    yield test_tasks_file

    # Restore the originals
    main.app.state.tasks_file = original_tasks_file
    main.app.state.root = original_root
    main.app.state.disable_auto_start = original_auto_start
    shutil.rmtree(test_dir)


@pytest.fixture
def git_repo():
    """A temporary git repo with tracked and gitignored files as the root."""
    # Create a temporary directory and initialize a git repo
    test_dir = tempfile.mkdtemp()
    orig_cwd = os.getcwd()
    original_root = main.app.state.root
    os.chdir(test_dir)
    main.app.state.root = pathlib.Path(test_dir).resolve()

    # Clear cached git repo check from previous tests
    paths.in_git_repo.cache_clear()

    subprocess.run(["git", "init"], check=True)
    subprocess.run(
        ["git", "config", "user.email", "you@example.com"], check=True
    )
    subprocess.run(["git", "config", "user.name", "Your Name"], check=True)

    # Create some files
    (pathlib.Path(test_dir) / "file1.txt").write_text("content1")
    (pathlib.Path(test_dir) / "dir1").mkdir()
    (pathlib.Path(test_dir) / "dir1" / "file2.txt").write_text("content2")

    # Create .gitignore and ignore some files
    (pathlib.Path(test_dir) / ".gitignore").write_text(
        "ignored.txt\nnode_modules/"
    )
    (pathlib.Path(test_dir) / "ignored.txt").write_text("should be ignored")
    (pathlib.Path(test_dir) / "node_modules").mkdir()
    (pathlib.Path(test_dir) / "node_modules" / "some_file.txt").write_text(
        "ignored"
    )

    yield pathlib.Path(test_dir)

    # Clear cached git repo check and restore cwd
    paths.in_git_repo.cache_clear()
    os.chdir(orig_cwd)
    main.app.state.root = original_root
    shutil.rmtree(test_dir)


@pytest.fixture
def non_git_dir():
    """A temporary directory that is NOT a git repo."""
    test_dir = tempfile.mkdtemp()
    orig_cwd = os.getcwd()
    original_root = main.app.state.root
    os.chdir(test_dir)
    main.app.state.root = pathlib.Path(test_dir).resolve()

    # Clear cached git repo check
    paths.in_git_repo.cache_clear()

    # Create files (including one that would be "ignored" if git were present)
    (pathlib.Path(test_dir) / "file1.txt").write_text("content1")
    (pathlib.Path(test_dir) / "ignored.txt").write_text("not actually ignored")

    yield pathlib.Path(test_dir)

    paths.in_git_repo.cache_clear()
    os.chdir(orig_cwd)
    main.app.state.root = original_root
    shutil.rmtree(test_dir)


@pytest.fixture
def temp_repo(tmp_path, monkeypatch):
    """Create a temporary directory and set it as the API root."""
    root = tmp_path / "repo"
    root.mkdir()

    # Mock main.app.state.root
    original_root = main.app.state.root
    main.app.state.root = root

    yield root

    # Restore original root
    main.app.state.root = original_root


@pytest.fixture
def test_workspace():
    """A temporary root with a subproject, with auto-start enabled."""
    # Create a temporary root directory
    root_dir = pathlib.Path(tempfile.mkdtemp()).resolve()

    # Create a subproject directory
    subproject_dir = root_dir / "my-subproject"
    subproject_dir.mkdir()

    # Set up some tasks in the subproject
    sub_tasks_file = subproject_dir / "tasks.yml"
    data = models.Roadmap(
        goal="Subproject goal",
        tasks=[
            models.Task(
                id="sub1",
                description="Sub Task 1",
                status=models.TaskStatus.PENDING,
            ),
        ],
    )
    persistence.save_tasks(sub_tasks_file, data)

    # Override app state
    original_root = main.app.state.root
    original_tasks_file = main.app.state.tasks_file
    original_auto_start = main.app.state.disable_auto_start

    main.app.state.root = root_dir
    main.app.state.tasks_file = root_dir / "tasks.yml"
    main.app.state.disable_auto_start = False  # Enable auto-start for testing

    yield root_dir, subproject_dir

    # Restore app state
    main.app.state.root = original_root
    main.app.state.tasks_file = original_tasks_file
    main.app.state.disable_auto_start = original_auto_start
    shutil.rmtree(root_dir)
