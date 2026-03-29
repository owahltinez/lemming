from .. import models, persistence
from . import outcomes


def test_add_outcome(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(tasks=[models.Task(id="1", description="Outcome test")])
    persistence.save_tasks(tasks_file, data)

    outcomes.add_outcome(tasks_file, "1", "Something happened")
    updated_data = persistence.load_tasks(tasks_file)
    assert "Something happened" in updated_data.tasks[0].outcomes


def test_delete_outcome(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1", description="Delete outcome test", outcomes=["0", "1", "2"]
            )
        ]
    )
    persistence.save_tasks(tasks_file, data)

    outcomes.delete_outcome(tasks_file, "1", 1)
    updated_data = persistence.load_tasks(tasks_file)
    assert updated_data.tasks[0].outcomes == ["0", "2"]


def test_edit_outcome(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[models.Task(id="1", description="Edit outcome test", outcomes=["old"])]
    )
    persistence.save_tasks(tasks_file, data)

    outcomes.edit_outcome(tasks_file, "1", 0, "new")
    updated_data = persistence.load_tasks(tasks_file)
    assert updated_data.tasks[0].outcomes == ["new"]
