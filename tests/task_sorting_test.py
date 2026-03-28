import time
from lemming import tasks


def test_completed_tasks_sorting_newest_first(tmp_path):
    tasks_file = tmp_path / "tasks.yml"

    # Add three tasks and mark them completed at different times
    tasks.add_task(tasks_file, "Task 1")
    tasks.add_task(tasks_file, "Task 2")
    tasks.add_task(tasks_file, "Task 3")

    # Manually update completion times to ensure order
    with tasks.lock_tasks(tasks_file):
        data = tasks.load_tasks(tasks_file)
        # Oldest
        data.tasks[0].status = tasks.TaskStatus.COMPLETED
        data.tasks[0].completed_at = 100.0
        # Middle
        data.tasks[1].status = tasks.TaskStatus.COMPLETED
        data.tasks[1].completed_at = 200.0
        # Newest
        data.tasks[2].status = tasks.TaskStatus.COMPLETED
        data.tasks[2].completed_at = 300.0
        tasks.save_tasks(tasks_file, data)

    project_data = tasks.get_project_data(tasks_file)
    completed_tasks = [
        t for t in project_data.tasks if t.status == tasks.TaskStatus.COMPLETED
    ]

    # Should be newest first
    assert completed_tasks[0].description == "Task 3"
    assert completed_tasks[1].description == "Task 2"
    assert completed_tasks[2].description == "Task 1"


def test_uncompleted_tasks_sorting_newest_first(tmp_path):
    tasks_file = tmp_path / "tasks.yml"

    # Add three tasks, some pending, some in progress
    tasks.add_task(tasks_file, "Task 1")
    tasks.add_task(tasks_file, "Task 2")
    tasks.add_task(tasks_file, "Task 3")

    with tasks.lock_tasks(tasks_file):
        data = tasks.load_tasks(tasks_file)
        # Task 2 in progress
        data.tasks[1].status = tasks.TaskStatus.IN_PROGRESS
        tasks.save_tasks(tasks_file, data)

    project_data = tasks.get_project_data(tasks_file)
    uncompleted_tasks = [
        t
        for t in project_data.tasks
        if t.status not in (tasks.TaskStatus.COMPLETED, tasks.TaskStatus.FAILED)
    ]

    # Should be newest first (regardless of in_progress status)
    # T3 (index 2), T2 (index 1), T1 (index 0)
    assert uncompleted_tasks[0].description == "Task 3"
    assert uncompleted_tasks[1].description == "Task 2"
    assert uncompleted_tasks[2].description == "Task 1"


def test_failed_tasks_sorting_grouped_with_completed(tmp_path):
    tasks_file = tmp_path / "tasks.yml"

    # Add four tasks: one pending, one in progress, one failed, one completed
    tasks.add_task(tasks_file, "Pending")
    tasks.add_task(tasks_file, "In Progress")
    tasks.add_task(tasks_file, "Failed")
    tasks.add_task(tasks_file, "Completed")

    with tasks.lock_tasks(tasks_file):
        data = tasks.load_tasks(tasks_file)
        # In Progress
        data.tasks[1].status = tasks.TaskStatus.IN_PROGRESS
        # Failed
        data.tasks[2].status = tasks.TaskStatus.FAILED
        # Completed
        data.tasks[3].status = tasks.TaskStatus.COMPLETED
        data.tasks[3].completed_at = time.time() + 1000.0
        tasks.save_tasks(tasks_file, data)

    project_data = tasks.get_project_data(tasks_file)

    # Order should be:
    # 1. In Progress (uncompleted, newest of the two)
    # 2. Pending (uncompleted, older)
    # 3. Completed (completed/failed, newest)
    # 4. Failed (completed/failed, older)

    assert project_data.tasks[0].description == "In Progress"
    assert project_data.tasks[1].description == "Pending"
    assert project_data.tasks[2].description == "Completed"
    assert project_data.tasks[3].description == "Failed"


def test_task_sorting_tie_breaker(tmp_path):
    tasks_file = tmp_path / "tasks.yml"

    # Add three tasks with the same creation time (roughly)
    # We'll manually set them to be exactly the same
    tasks.add_task(tasks_file, "Task A")
    tasks.add_task(tasks_file, "Task B")
    tasks.add_task(tasks_file, "Task C")

    with tasks.lock_tasks(tasks_file):
        data = tasks.load_tasks(tasks_file)
        now = time.time()
        for t in data.tasks:
            t.created_at = now
        tasks.save_tasks(tasks_file, data)

    project_data = tasks.get_project_data(tasks_file)

    # Should be reverse chronological by index: Task C, Task B, Task A
    assert project_data.tasks[0].description == "Task C"
    assert project_data.tasks[1].description == "Task B"
    assert project_data.tasks[2].description == "Task A"
