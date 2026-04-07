from lemming import models
from lemming.tasks import progress


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
    from lemming import persistence

    persistence.save_tasks(tasks_file, data)

    target = progress.add_progress(tasks_file, "123", "Found bug in module X")

    assert target.id == "123"
    assert len(target.progress) == 1
    assert target.progress[0] == "Found bug in module X"
