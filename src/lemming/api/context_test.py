import pathlib
import pytest
import fastapi
from lemming.api import context


class MockAppState:
    def __init__(self, root: pathlib.Path, tasks_file: pathlib.Path):
        self.root = root
        self.tasks_file = tasks_file


def test_resolve_project_dir(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    project = root / "project"
    project.mkdir()

    app_state = MockAppState(root, root / "tasks.yml")

    # Default (no project)
    assert context.resolve_project_dir(app_state) == root

    # Specific project
    assert context.resolve_project_dir(app_state, "project") == project

    # Outside root
    with pytest.raises(fastapi.HTTPException) as excinfo:
        context.resolve_project_dir(app_state, "..")
    assert excinfo.value.status_code == 403

    # Not a directory
    not_dir = root / "file.txt"
    not_dir.write_text("hello")
    with pytest.raises(fastapi.HTTPException) as excinfo:
        context.resolve_project_dir(app_state, "file.txt")
    assert excinfo.value.status_code == 400


def test_resolve_tasks_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    project = root / "project"
    project.mkdir()

    default_tasks = root / "tasks.yml"
    app_state = MockAppState(root, default_tasks)

    # Default
    assert context.resolve_tasks_file(app_state) == default_tasks

    # Project (should use get_tasks_file_for_dir logic)
    # Since project / tasks.yml doesn't exist, it should be in lemming home with a hash
    tasks_file = context.resolve_tasks_file(app_state, "project")
    assert tasks_file.name == "tasks.yml"
    assert tasks_file.parent.name != "project"  # It's a hash, not the project name
    assert tasks_file.is_absolute()
