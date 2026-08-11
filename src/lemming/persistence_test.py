import os
import subprocess
import time

import pytest
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


def test_load_tasks_without_superseded_fields(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(
        yaml.safe_dump(
            {
                "tasks": [
                    {
                        "id": "1",
                        "description": "task from an older roadmap",
                        "status": "pending",
                        "attempts": 0,
                    }
                ]
            }
        )
    )

    loaded = persistence.load_tasks(tasks_file)

    assert loaded.tasks[0].superseded_at is None
    assert loaded.tasks[0].superseded_reason is None


def test_load_tasks_preserves_explicit_runner(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(yaml.dump({"config": {"runner": "aider"}}))

    loaded = persistence.load_tasks(tasks_file)
    assert loaded.config.runner == "aider"


CORRUPT_YAML = "tasks: [unclosed\n"
OTHER_CORRUPT_YAML = "goal: 'unterminated\n"


def _write_roadmap(tasks_file):
    """Persists a roadmap holding one real task."""
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            goal="ship it",
            tasks=[models.Task(id="1", description="task 1")],
        ),
    )


def test_load_tasks_raises_on_corrupted_file(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(CORRUPT_YAML, encoding="utf-8")

    with pytest.raises(persistence.CorruptedTasksError) as excinfo:
        persistence.load_tasks(tasks_file)

    error = excinfo.value
    assert str(tasks_file) in str(error)
    assert str(error.backup_file) in str(error)
    assert error.tasks_file == tasks_file
    assert isinstance(error.error, yaml.YAMLError)


def test_load_tasks_backs_up_corrupted_file(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(CORRUPT_YAML, encoding="utf-8")

    with pytest.raises(persistence.CorruptedTasksError) as excinfo:
        persistence.load_tasks(tasks_file)

    backup = tmp_path / "tasks.yml.corrupt"
    assert excinfo.value.backup_file == backup
    assert backup.read_text(encoding="utf-8") == CORRUPT_YAML
    # The unreadable original stays exactly where the user can find it.
    assert tasks_file.read_text(encoding="utf-8") == CORRUPT_YAML


def test_load_tasks_reuses_backup_for_same_corruption(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(CORRUPT_YAML, encoding="utf-8")

    for _ in range(3):
        with pytest.raises(persistence.CorruptedTasksError):
            persistence.load_tasks(tasks_file)

    backups = sorted(p.name for p in tmp_path.glob("tasks.yml.corrupt*"))
    assert backups == ["tasks.yml.corrupt"]


def test_load_tasks_keeps_earlier_corrupt_backup(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(CORRUPT_YAML, encoding="utf-8")
    with pytest.raises(persistence.CorruptedTasksError):
        persistence.load_tasks(tasks_file)

    # A second, different corruption must not destroy the first backup.
    tasks_file.write_text(OTHER_CORRUPT_YAML, encoding="utf-8")
    with pytest.raises(persistence.CorruptedTasksError) as excinfo:
        persistence.load_tasks(tasks_file)

    first_backup = tmp_path / "tasks.yml.corrupt"
    second_backup = tmp_path / "tasks.yml.corrupt.1"
    assert first_backup.read_text(encoding="utf-8") == CORRUPT_YAML
    assert second_backup.read_text(encoding="utf-8") == OTHER_CORRUPT_YAML
    assert excinfo.value.backup_file == second_backup


def test_load_tasks_skips_an_unusable_backup_path(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(CORRUPT_YAML, encoding="utf-8")
    # Something else already owns the preferred backup name.
    (tmp_path / "tasks.yml.corrupt").mkdir()

    with pytest.raises(persistence.CorruptedTasksError) as excinfo:
        persistence.load_tasks(tasks_file)

    assert excinfo.value.backup_file == tmp_path / "tasks.yml.corrupt.1"
    assert (tmp_path / "tasks.yml.corrupt").is_dir()


def test_load_tasks_reports_when_backup_is_impossible(tmp_path, monkeypatch):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text(CORRUPT_YAML, encoding="utf-8")
    monkeypatch.setattr(persistence, "_backup_corrupted_tasks", lambda *_: None)

    with pytest.raises(persistence.CorruptedTasksError) as excinfo:
        persistence.load_tasks(tasks_file)

    assert excinfo.value.backup_file is None
    assert "left untouched" in str(excinfo.value)


def test_load_tasks_rejects_undecodable_file(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_bytes(b"goal: \xff\xfe not utf-8\n")

    with pytest.raises(persistence.CorruptedTasksError):
        persistence.load_tasks(tasks_file)

    assert (tmp_path / "tasks.yml.corrupt").read_bytes() == (
        b"goal: \xff\xfe not utf-8\n"
    )


def test_load_tasks_missing_file_returns_default_roadmap(tmp_path):
    loaded = persistence.load_tasks(tmp_path / "missing.yml")

    assert loaded.tasks == []
    assert "Long-Term Goal" in loaded.goal
    assert not list(tmp_path.iterdir())


def test_load_tasks_empty_file_returns_default_roadmap(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    tasks_file.write_text("", encoding="utf-8")

    loaded = persistence.load_tasks(tasks_file)

    assert loaded.tasks == []
    assert not list(tmp_path.glob("*.corrupt*"))


def test_corrupted_file_is_not_overwritten_by_load_mutate_save(tmp_path):
    """The reported incident: a corrupt roadmap must never be replaced.

    Every mutation goes through load -> mutate -> save. When the load raises,
    the save never runs, so the bytes on disk stay recoverable.
    """
    tasks_file = tmp_path / "tasks.yml"
    _write_roadmap(tasks_file)
    tasks_file.write_text(CORRUPT_YAML, encoding="utf-8")

    with pytest.raises(persistence.CorruptedTasksError):
        with persistence.lock_tasks(tasks_file):
            data = persistence.load_tasks(tasks_file)
            data.tasks.append(models.Task(id="2", description="task 2"))
            persistence.save_tasks(tasks_file, data)

    assert tasks_file.read_text(encoding="utf-8") == CORRUPT_YAML
    assert (tmp_path / "tasks.yml.corrupt").read_text() == CORRUPT_YAML


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


def test_acquire_loop_lock_rejects_live_owner(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.acquire_loop_lock(tasks_file)

    with pytest.raises(
        persistence.LoopAlreadyRunningError, match=str(os.getpid())
    ):
        persistence.acquire_loop_lock(tasks_file)

    persistence.release_loop_lock(tasks_file)


def test_is_loop_running_clears_stale_lock(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    lock_path = persistence._get_loop_lock_path(tasks_file)
    lock_path.write_text("999999")

    assert persistence.is_loop_running(tasks_file) is False
    assert not lock_path.exists()


def test_get_loop_pid_corrupted_lock_file(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    lock_path = persistence._get_loop_lock_path(tasks_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Write "corrupted" content
    lock_path.write_text("not-a-pid")
    assert persistence.get_loop_pid(tasks_file) is None
    assert persistence.is_loop_running(tasks_file) is False
    assert not lock_path.exists()


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


def test_is_pid_alive_treats_unreaped_child_as_dead():
    """An exited-but-unreaped process must not read as alive.

    A supervisor that starts the loop and later calls `lemming stop` still
    owns the zombie, so treating it as alive would hang the stop.
    """
    child = subprocess.Popen(["true"])
    try:
        # Let it exit, but do not poll(): that would reap it and remove the
        # zombie this test is about.
        time.sleep(1.0)

        assert persistence.is_pid_alive(child.pid) is False
    finally:
        child.wait()


def test_is_pid_alive_true_for_running_process():
    assert persistence.is_pid_alive(os.getpid()) is True
