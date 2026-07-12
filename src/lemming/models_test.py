import pydantic
import pytest

from lemming import models


def test_task_status_values():
    assert models.TaskStatus.PENDING == "pending"
    assert models.TaskStatus.IN_PROGRESS == "in_progress"
    assert models.TaskStatus.COMPLETED == "completed"
    assert models.TaskStatus.FAILED == "failed"


def test_task_model_defaults():
    task = models.Task(id="123", description="Test")
    assert task.status == models.TaskStatus.PENDING
    assert task.attempts == 0
    assert task.progress == []
    assert task.run_time == 0.0
    assert task.created_at > 0


def test_task_model_validation():
    # description is required
    with pytest.raises(pydantic.ValidationError):
        models.Task(id="123")

    # id is required
    with pytest.raises(pydantic.ValidationError):
        models.Task(description="Test")


def test_roadmap_defaults():
    roadmap = models.Roadmap()
    assert roadmap.context == ""
    assert roadmap.tasks == []
    assert isinstance(roadmap.config, models.RoadmapConfig)


def test_roadmap_config_defaults():
    config = models.RoadmapConfig()
    assert config.retries == 3
    assert config.runner in models.KNOWN_RUNNERS
    assert config.hooks is None
    assert config.time_limit == 60


def test_detect_default_runner_picks_first_installed(monkeypatch):
    models.detect_default_runner.cache_clear()
    monkeypatch.setattr(
        models.shutil, "which", lambda name: "/bin/x" if name == "claude" else None
    )
    assert models.detect_default_runner() == "claude"
    models.detect_default_runner.cache_clear()


def test_detect_default_runner_falls_back_to_agy(monkeypatch):
    models.detect_default_runner.cache_clear()
    monkeypatch.setattr(models.shutil, "which", lambda name: None)
    assert models.detect_default_runner() == "agy"
    models.detect_default_runner.cache_clear()


def test_detect_default_runner_prefers_agy(monkeypatch):
    models.detect_default_runner.cache_clear()
    monkeypatch.setattr(models.shutil, "which", lambda name: f"/bin/{name}")
    assert models.detect_default_runner() == "agy"
    models.detect_default_runner.cache_clear()


def test_roadmap_config_uses_detected_runner(monkeypatch):
    models.detect_default_runner.cache_clear()
    monkeypatch.setattr(
        models.shutil, "which", lambda name: "/bin/x" if name == "codex" else None
    )
    assert models.RoadmapConfig().runner == "codex"
    # An explicit value always wins over detection.
    assert models.RoadmapConfig(runner="aider").runner == "aider"
    models.detect_default_runner.cache_clear()


def test_roadmap_config_custom_time_limit():
    config = models.RoadmapConfig(time_limit=30)
    assert config.time_limit == 30

    config_disabled = models.RoadmapConfig(time_limit=0)
    assert config_disabled.time_limit == 0
