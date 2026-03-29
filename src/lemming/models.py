import enum
import time

import pydantic


class TaskStatus(enum.StrEnum):
    """Enumeration of possible task statuses."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(pydantic.BaseModel):
    """Represents a single task in the roadmap."""

    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    outcomes: list[str] = pydantic.Field(default_factory=list)
    runner: str | None = None
    completed_at: float | None = None
    started_at: float | None = None
    last_started_at: float | None = None
    created_at: float = pydantic.Field(default_factory=time.time)
    run_time: float = 0.0
    pid: int | None = None
    last_heartbeat: float | None = None
    has_runner_log: bool = False
    parent: str | None = None
    parent_tasks_file: str | None = None
    index: int | None = pydantic.Field(default=None)
    requested_status: TaskStatus | None = None


class RoadmapConfig(pydantic.BaseModel):
    """Configuration for the roadmap execution loop."""

    retries: int = 3
    runner: str = "gemini"
    hooks: list[str] | None = None


class Roadmap(pydantic.BaseModel):
    """Represents the entire roadmap state."""

    context: str = ""
    tasks: list[Task] = pydantic.Field(default_factory=list)
    config: RoadmapConfig = pydantic.Field(default_factory=RoadmapConfig)


class ProjectData(pydantic.BaseModel):
    """Represents the project data returned by the API."""

    context: str
    tasks: list[Task]
    config: RoadmapConfig
    cwd: str
    loop_running: bool
