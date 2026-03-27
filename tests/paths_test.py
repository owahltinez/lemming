import os
import pathlib
from lemming import paths


def test_get_lemming_home_default():
    expected = pathlib.Path.home() / ".local" / "lemming"
    if "LEMMING_HOME" in os.environ:
        del os.environ["LEMMING_HOME"]
    assert paths.get_lemming_home() == expected


def test_get_lemming_home_override(tmp_path):
    os.environ["LEMMING_HOME"] = str(tmp_path)
    assert paths.get_lemming_home() == tmp_path
    del os.environ["LEMMING_HOME"]


def test_get_project_dir(tmp_path):
    tasks_file = tmp_path / "my_tasks.yml"
    tasks_file.touch()

    project_dir = paths.get_project_dir(tasks_file)
    assert str(paths.get_lemming_home()) in str(project_dir)

    # Check that it's consistent
    assert paths.get_project_dir(tasks_file) == project_dir


def test_get_default_tasks_file_local(tmp_path):
    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        local_tasks = tmp_path / "tasks.yml"
        local_tasks.touch()
        assert paths.get_default_tasks_file() == tmp_path / "tasks.yml"
    finally:
        os.chdir(orig_cwd)


def test_get_log_file(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    log_file = paths.get_log_file(tasks_file, "task123")
    assert log_file.name == "task123-runner.log"
    assert log_file.parent.exists()


def test_in_git_repo_and_is_ignored(tmp_path):
    import subprocess

    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        if hasattr(paths.in_git_repo, "_result"):
            delattr(paths.in_git_repo, "_result")

        # Not a git repo yet
        assert paths.in_git_repo() is False
        assert paths.is_ignored(tmp_path / "foo.txt") is False

        # Init git repo
        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True)

        # Clear cache again
        if hasattr(paths.in_git_repo, "_result"):
            delattr(paths.in_git_repo, "_result")

        assert paths.in_git_repo() is True

        # Create a gitignore
        (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (tmp_path / "ignored.txt").touch()
        (tmp_path / "not_ignored.txt").touch()

        assert paths.is_ignored(tmp_path / "ignored.txt") is True
        assert paths.is_ignored(tmp_path / "not_ignored.txt") is False

    finally:
        os.chdir(orig_cwd)
