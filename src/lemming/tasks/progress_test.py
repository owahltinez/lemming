from .. import models, persistence
from . import progress


def test_add_progress(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(tasks=[models.Task(id="1", description="Progress test")])
    persistence.save_tasks(tasks_file, data)

    progress.add_progress(tasks_file, "1", "Something happened")
    updated_data = persistence.load_tasks(tasks_file)
    assert "Something happened" in updated_data.tasks[0].progress


def test_delete_progress(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1", description="Delete progress test", progress=["0", "1", "2"]
            )
        ]
    )
    persistence.save_tasks(tasks_file, data)

    progress.delete_progress(tasks_file, "1", 1)
    updated_data = persistence.load_tasks(tasks_file)
    assert updated_data.tasks[0].progress == ["0", "2"]


def test_edit_progress(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[models.Task(id="1", description="Edit progress test", progress=["old"])]
    )
    persistence.save_tasks(tasks_file, data)

    progress.edit_progress(tasks_file, "1", 0, "new")
    updated_data = persistence.load_tasks(tasks_file)
    assert updated_data.tasks[0].progress == ["new"]
