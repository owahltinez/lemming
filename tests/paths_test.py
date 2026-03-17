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
        assert paths.get_default_tasks_file() == pathlib.Path("tasks.yml")
    finally:
        os.chdir(orig_cwd)


def test_get_log_file(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    log_file = paths.get_log_file(tasks_file, "task123")
    assert log_file.name == "task123.log"
    assert log_file.parent.exists()
