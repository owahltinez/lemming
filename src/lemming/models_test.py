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
    assert task.outcomes == []
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
    assert config.runner == "gemini"
    assert config.hooks is None
