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
        data.tasks[0].status = "completed"
        data.tasks[0].completed_at = 100.0
        # Middle
        data.tasks[1].status = "completed"
        data.tasks[1].completed_at = 200.0
        # Newest
        data.tasks[2].status = "completed"
        data.tasks[2].completed_at = 300.0
        tasks.save_tasks(tasks_file, data)

    project_data = tasks.get_project_data(tasks_file)
    completed_tasks = [t for t in project_data.tasks if t.status == "completed"]

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
        data.tasks[1].status = "in_progress"
        tasks.save_tasks(tasks_file, data)

    project_data = tasks.get_project_data(tasks_file)
    uncompleted_tasks = [t for t in project_data.tasks if t.status != "completed"]

    # Should be newest first (regardless of in_progress status)
    # T3 (index 2), T2 (index 1), T1 (index 0)
    assert uncompleted_tasks[0].description == "Task 3"
    assert uncompleted_tasks[1].description == "Task 2"
    assert uncompleted_tasks[2].description == "Task 1"
