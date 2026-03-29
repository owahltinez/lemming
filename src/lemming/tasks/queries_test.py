import time
from lemming import paths
from .. import models, persistence
from . import queries


def test_get_project_data_deduplication(tmp_path):
    tasks_file = tmp_path / "tasks.yml"

    # Create a corrupted roadmap with duplicate task IDs
    data = models.Roadmap(
        context="test",
        tasks=[
            models.Task(id="1", description="Task 1", status=models.TaskStatus.PENDING),
            models.Task(
                id="1", description="Task 1 Duplicate", status=models.TaskStatus.PENDING
            ),
            models.Task(id="2", description="Task 2", status=models.TaskStatus.PENDING),
        ],
    )
    persistence.save_tasks(tasks_file, data)

    project_data = queries.get_project_data(tasks_file)

    # Should only have two unique tasks, oldest first (chronological)
    assert len(project_data.tasks) == 2
    assert project_data.tasks[0].description == "Task 1"
    assert project_data.tasks[1].description == "Task 2"


def test_get_project_data_enriches_metadata(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(id="1", description="Task 1", status=models.TaskStatus.PENDING),
            models.Task(id="2", description="Task 2", status=models.TaskStatus.PENDING),
        ]
    )
    persistence.save_tasks(tasks_file, data)

    # Create a dummy log file for Task 2
    log_file = paths.get_log_file(tasks_file, "2")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("dummy log")

    project_data = queries.get_project_data(tasks_file)

    # Check index and has_runner_log
    t2 = next(t for t in project_data.tasks if t.id == "2")
    t1 = next(t for t in project_data.tasks if t.id == "1")

    assert t1.index == 0
    assert t1.has_runner_log is False

    assert t2.index == 1
    assert t2.has_runner_log is True


def test_completed_tasks_sorting_newest_first(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.COMPLETED,
                completed_at=100.0,
            ),
            models.Task(
                id="2",
                description="Task 2",
                status=models.TaskStatus.COMPLETED,
                completed_at=200.0,
            ),
            models.Task(
                id="3",
                description="Task 3",
                status=models.TaskStatus.COMPLETED,
                completed_at=300.0,
            ),
        ]
    )
    persistence.save_tasks(tasks_file, data)

    project_data = queries.get_project_data(tasks_file)
    completed_tasks = [
        t for t in project_data.tasks if t.status == models.TaskStatus.COMPLETED
    ]

    # Should be newest first
    assert completed_tasks[0].description == "Task 3"
    assert completed_tasks[1].description == "Task 2"
    assert completed_tasks[2].description == "Task 1"


def test_uncompleted_tasks_sorting_prioritizes_in_progress(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.PENDING,
                created_at=1.0,
            ),
            models.Task(
                id="2",
                description="Task 2",
                status=models.TaskStatus.IN_PROGRESS,
                created_at=2.0,
            ),
            models.Task(
                id="3",
                description="Task 3",
                status=models.TaskStatus.PENDING,
                created_at=3.0,
            ),
        ]
    )
    persistence.save_tasks(tasks_file, data)

    project_data = queries.get_project_data(tasks_file)
    uncompleted_tasks = [
        t
        for t in project_data.tasks
        if t.status not in (models.TaskStatus.COMPLETED, models.TaskStatus.FAILED)
    ]

    # Should prioritize in_progress first, then chronological index
    # Task 2 (in_progress), Task 1 (index 0), Task 3 (index 2)
    assert uncompleted_tasks[0].description == "Task 2"
    assert uncompleted_tasks[1].description == "Task 1"
    assert uncompleted_tasks[2].description == "Task 3"


def test_failed_tasks_sorting_grouped_with_completed(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    now = time.time()
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Pending",
                status=models.TaskStatus.PENDING,
                created_at=now,
            ),
            models.Task(
                id="2",
                description="In Progress",
                status=models.TaskStatus.IN_PROGRESS,
                created_at=now + 1,
            ),
            models.Task(
                id="3",
                description="Failed",
                status=models.TaskStatus.FAILED,
                created_at=now + 2,
            ),
            models.Task(
                id="4",
                description="Completed",
                status=models.TaskStatus.COMPLETED,
                completed_at=now + 1000.0,
                created_at=now + 3,
            ),
        ]
    )
    persistence.save_tasks(tasks_file, data)

    project_data = queries.get_project_data(tasks_file)

    # Order should be:
    # 1. In Progress (uncompleted, prioritized)
    # 2. Pending (uncompleted, index 0)
    # 3. Completed (completed/failed, newest)
    # 4. Failed (completed/failed, older)

    assert project_data.tasks[0].description == "In Progress"
    assert project_data.tasks[1].description == "Pending"
    assert project_data.tasks[2].description == "Completed"
    assert project_data.tasks[3].description == "Failed"


def test_get_pending_task(tmp_path):
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.PENDING,
                created_at=1.0,
            ),
            models.Task(
                id="2",
                description="Task 2",
                status=models.TaskStatus.PENDING,
                created_at=2.0,
            ),
        ]
    )
    pending = queries.get_pending_task(data)
    assert pending.id == "1"


def test_get_pending_task_none_if_in_progress(tmp_path):
    data = models.Roadmap(
        tasks=[
            models.Task(
                id="1",
                description="Task 1",
                status=models.TaskStatus.IN_PROGRESS,
                last_heartbeat=time.time(),
            )
        ]
    )
    pending = queries.get_pending_task(data)
    assert pending is None
