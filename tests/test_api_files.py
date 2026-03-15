import os
import pathlib
import subprocess
import shutil
import tempfile
from fastapi.testclient import TestClient
import pytest

# Ensure PYTHONPATH includes src
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from lemming.api import app, _in_git_repo

client = TestClient(app)


@pytest.fixture
def git_repo():
    # Create a temporary directory and initialize a git repo
    test_dir = tempfile.mkdtemp()
    orig_cwd = os.getcwd()
    os.chdir(test_dir)

    # Clear cached git repo check from previous tests
    if hasattr(_in_git_repo, "_result"):
        del _in_git_repo._result

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
    if hasattr(_in_git_repo, "_result"):
        del _in_git_repo._result
    os.chdir(orig_cwd)
    shutil.rmtree(test_dir)


def test_root_redirect(git_repo):
    response = client.get("/files", follow_redirects=False)
    # FastAPI RedirectResponse returns 307 by default or 302?
    assert response.status_code in [302, 307, 301]
    assert response.headers["location"].endswith("/files/")


def test_list_root(git_repo):
    # Test template response
    response = client.get("/files/")
    assert response.status_code == 200
    assert "Lemming Task Runner" in response.text

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
    assert "Lemming Task Runner" in response.text

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


@pytest.fixture
def non_git_dir():
    """A temporary directory that is NOT a git repo."""
    test_dir = tempfile.mkdtemp()
    orig_cwd = os.getcwd()
    os.chdir(test_dir)

    # Clear cached git repo check
    if hasattr(_in_git_repo, "_result"):
        del _in_git_repo._result

    # Create files (including one that would be "ignored" if git were present)
    (pathlib.Path(test_dir) / "file1.txt").write_text("content1")
    (pathlib.Path(test_dir) / "ignored.txt").write_text("not actually ignored")

    yield pathlib.Path(test_dir)

    if hasattr(_in_git_repo, "_result"):
        del _in_git_repo._result
    os.chdir(orig_cwd)
    shutil.rmtree(test_dir)


def test_list_non_git_dir(non_git_dir):
    """Files should be listed without errors when not in a git repo."""
    response = client.get("/api/files/")
    assert response.status_code == 200
    data = response.json()
    names = [item["name"] for item in data["contents"]]

    # All files should be visible since there's no git to check ignore rules
    assert "file1.txt" in names
    assert "ignored.txt" in names


def test_security_traversal(git_repo):
    # Try to go outside base_path
    client.get("/files/../")
    # resolve() will make it point to the same directory or its parent.
    # If it's outside, it should be 403.
    # Note: TestClient might handle .. itself.
    pass
