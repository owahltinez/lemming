import pytest

from lemming import models, paths, persistence
from lemming.tasks import limits, progress


def test_add_progress(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="123",
                description="Task 1",
            )
        ]
    )
    # create file to test load/save logic
    persistence.save_tasks(tasks_file, data)

    target = progress.add_progress(tasks_file, "123", "Found bug in module X")

    assert target.id == "123"
    assert len(target.progress) == 1
    assert target.progress[0] == "Found bug in module X"


def test_add_progress_enforces_entry_size_limit(tmp_path, monkeypatch):
    lemming_home = tmp_path / "lemming-home"
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))
    tasks_file = tmp_path / "project" / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(tasks=[models.Task(id="123", description="Task")]),
    )

    accepted = progress.add_progress(
        tasks_file,
        "123",
        "a" * limits.MAX_PROGRESS_ENTRY_CHARS,
    )
    assert len(accepted.progress[0]) == limits.MAX_PROGRESS_ENTRY_CHARS

    with pytest.raises(ValueError) as excinfo:
        progress.add_progress(
            tasks_file,
            "123",
            "b" * (limits.MAX_PROGRESS_ENTRY_CHARS + 1),
        )

    message = str(excinfo.value)
    assert "281 characters (limit 280)" in message
    assert "Record the finding in one line" in message
    assert str(paths.get_project_dir(tasks_file)) in message
    assert len(persistence.load_tasks(tasks_file).tasks[0].progress) == 1
