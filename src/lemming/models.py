"""Pydantic models for tasks, roadmap state, and runner configuration."""

import enum
import functools
import os
import shutil
import sys
import time

import pydantic

# Runner CLIs the orchestrator knows how to drive, in default-preference order.
KNOWN_RUNNERS = ("agy", "opencode", "claude", "codex")


def runner_path() -> str:
    """Returns PATH with lemming's own directory and ~/.local/bin appended.

    Agent subshells often inherit a minimal PATH that omits where uv installs
    lemming and the runner CLIs. Directories already present keep their order.
    """
    dirs = os.get_exec_path()
    lemming_dir = os.path.dirname(sys.executable)
    for extra in (lemming_dir, os.path.expanduser("~/.local/bin")):
        if os.path.isdir(extra) and extra not in dirs:
            dirs.append(extra)
    return os.pathsep.join(dirs)


@functools.cache
def detect_default_runner() -> str:
    """Returns the first known runner CLI found on PATH, falling back to "agy".

    Only used when no runner is persisted in the tasks file; an explicit
    (user-set) runner always takes precedence and remains sticky.
    """
    path = runner_path()
    for name in KNOWN_RUNNERS:
        if shutil.which(name, path=path):
            return name
    return KNOWN_RUNNERS[0]


class TaskNotFoundError(ValueError):
    """Raised when a task ID cannot be found in the roadmap."""


class AmbiguousTaskIdError(ValueError):
    """Raised when a task ID prefix matches more than one task."""


class TaskStatus(enum.StrEnum):
    """Phases of a task, moved between by tasks/state_machine.py."""

    PENDING = "pending"
    RUNNING = "running"
    # The agent has finished; hooks are reviewing the outcome.
    COMPLETING = "completing"
    FAILING = "failing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"

    @property
    def outcome(self) -> "TaskStatus":
        """The status a finalizing task is heading for, else this one."""
        return _OUTCOMES.get(self, self)


_OUTCOMES = {
    TaskStatus.COMPLETING: TaskStatus.COMPLETED,
    TaskStatus.FAILING: TaskStatus.FAILED,
}
# Statuses whose task has an owner working on it.
ACTIVE_STATUSES = frozenset(
    {TaskStatus.RUNNING, TaskStatus.COMPLETING, TaskStatus.FAILING}
)
TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.SUPERSEDED,
    }
)


class Task(pydantic.BaseModel):
    """Represents a single task in the roadmap."""

    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    progress: list[str] = pydantic.Field(default_factory=list)
    runner: str | None = None
    model: str | None = None
    # Atomic tasks (typo fix, version bump) skip post-task hooks on success.
    oneshot: bool = False
    # Review hooks (below the 9x band) for this task; None uses the project's.
    hooks: list[str] | None = None
    # Runner command of the most recent attempt, with the prompt elided. Set
    # when the runner starts so provenance survives the process.
    resolved_command: str | None = None
    completed_at: float | None = None
    superseded_at: float | None = None
    superseded_reason: str | None = None
    started_at: float | None = None
    last_started_at: float | None = None
    created_at: float = pydantic.Field(default_factory=time.time)
    run_time: float = 0.0
    execution_times: dict[str, float] | None = None
    active_execution_component: str | None = None
    active_execution_started_at: float | None = None
    pid: int | None = None
    last_heartbeat: float | None = None
    has_runner_log: bool = False
    parent: str | None = None
    parent_tasks_file: str | None = None
    index: int | None = pydantic.Field(default=None)
    # Set by a finalization hook that found a defect it could not fix.
    # Blocks the requested completion and is cleared on every retry.
    rejection: str | None = None
    pre_tree: str | None = None
    runner_tree: str | None = None
    post_tree: str | None = None
    pre_head: str | None = None
    diff_summary: str | None = None
    hook_trees: dict[str, list[str]] | None = None

    @pydantic.model_validator(mode="before")
    @classmethod
    def _merge_requested_status(cls, data):
        """Maps the status pair older roadmaps stored onto a single status."""
        if not isinstance(data, dict) or not (
            "requested_status" in data or data.get("status") == "in_progress"
        ):
            return data
        data = dict(data)
        requested = data.pop("requested_status", None)
        if data.get("status") == "in_progress":
            data["status"] = {
                "completed": TaskStatus.COMPLETING,
                "failed": TaskStatus.FAILING,
                "cancelled": TaskStatus.CANCELLED,
                "superseded": TaskStatus.SUPERSEDED,
            }.get(requested, TaskStatus.RUNNING)
        return data


class RoadmapConfig(pydantic.BaseModel):
    """Configuration for the roadmap execution loop."""

    retries: int = 3
    runner: str = pydantic.Field(default_factory=detect_default_runner)
    model: str | None = None
    time_limit: int = 60
    # Hooks enabled by default; reviews are opt-in, so only the 9x band.
    hooks: list[str] = pydantic.Field(default_factory=lambda: ["roadmap"])


class Roadmap(pydantic.BaseModel):
    """Represents the entire roadmap state."""

    goal: str = ""
    tasks: list[Task] = pydantic.Field(default_factory=list)
    config: RoadmapConfig = pydantic.Field(default_factory=RoadmapConfig)


class ProjectData(pydantic.BaseModel):
    """Represents the project data returned by the API."""

    goal: str
    tasks: list[Task]
    config: RoadmapConfig
    cwd: str
    loop_running: bool
