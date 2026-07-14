import os

import yaml

from lemming import models, persistence


def test_lock_tasks(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    with persistence.lock_tasks(tasks_file):
        assert tasks_file.exists()
        assert tasks_file.read_text() == "{}"

    lock_path = tasks_file.with_suffix(".lock")
    assert lock_path.exists()


def test_load_save_tasks(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    data = models.Roadmap(
        goal="test",
        tasks=[
            models.Task(
                id="1",
                description="task 1",
                status=models.TaskStatus.PENDING,
                attempts=0,
            )
        ],
    )
    persistence.save_tasks(tasks_file, data)

    loaded = persistence.load_tasks(tasks_file)
    assert loaded.goal == "test"
    assert len(loaded.tasks) == 1
    assert loaded.tasks[0].id == "1"


def test_load_tasks_migrates_deprecated_gemini_runner(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(
        yaml.dump({"config": {"runner": "gemini", "retries": 5}})
    )

    loaded = persistence.load_tasks(tasks_file)
    # The deprecated runner is dropped so runtime detection supplies the
    # default.
    assert loaded.config.runner in models.KNOWN_RUNNERS
    # Other user-set config values are preserved.
    assert loaded.config.retries == 5


def test_load_tasks_migrates_deprecated_gemini_task_runner(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(
        yaml.dump(
            {
                "tasks": [
                    {"id": "1", "description": "a", "runner": "gemini"},
                    {"id": "2", "description": "b", "runner": "aider"},
                ]
            }
        )
    )

    loaded = persistence.load_tasks(tasks_file)
    # The deprecated per-task runner is dropped so the config runner is used.
    assert loaded.tasks[0].runner is None
    assert loaded.tasks[1].runner == "aider"


def test_load_tasks_preserves_explicit_runner(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(yaml.dump({"config": {"runner": "aider"}}))

    loaded = persistence.load_tasks(tasks_file)
    assert loaded.config.runner == "aider"


def test_loop_lock_management(tmp_path):
    tasks_file = tmp_path / "tasks.yml"

    # Initially, no loop is running
    assert persistence.get_loop_pid(tasks_file) is None

    # Create a lock file
    persistence.acquire_loop_lock(tasks_file)
    pid = persistence.get_loop_pid(tasks_file)
    assert pid == os.getpid()

    # Release the lock
    persistence.release_loop_lock(tasks_file)
    assert persistence.get_loop_pid(tasks_file) is None


def test_get_loop_pid_corrupted_lock_file(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    lock_path = persistence._get_loop_lock_path(tasks_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Write "corrupted" content
    lock_path.write_text("not-a-pid")
    assert persistence.get_loop_pid(tasks_file) is None


def test_save_tasks_excludes_computed_fields(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = models.Task(
        id="123",
        description="Test Task",
        index=5,
        has_runner_log=True,
    )
    roadmap = models.Roadmap(tasks=[task])

    persistence.save_tasks(tasks_file, roadmap)

    # Read the raw YAML to verify exclusion
    with open(tasks_file, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f)

    task_data = raw_data["tasks"][0]
    assert "id" in task_data
    assert "description" in task_data
    # Computed fields should be excluded
    assert "index" not in task_data
    assert "has_runner_log" not in task_data


def test_save_tasks_uses_block_style_for_multiline_strings(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    multiline_description = "Line 1\nLine 2\nLine 3"
    task = models.Task(
        id="123",
        description=multiline_description,
    )
    roadmap = models.Roadmap(tasks=[task])

    persistence.save_tasks(tasks_file, roadmap)

    # Read the raw content to check for '|'
    content = tasks_file.read_text(encoding="utf-8")
    assert "description: |" in content
    assert "Line 1" in content
    assert "Line 2" in content
    assert "Line 3" in content

    # Verify that it still loads correctly
    loaded = persistence.load_tasks(tasks_file)
    assert loaded.tasks[0].description == multiline_description
