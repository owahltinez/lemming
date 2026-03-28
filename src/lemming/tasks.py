import contextlib
import fcntl
import os
import pathlib
import secrets
import time

import pydantic
import yaml

from . import paths

STALE_THRESHOLD = 30  # seconds
LOOP_LOCK_FILENAME = ".lemming_loop.lock"


class Task(pydantic.BaseModel):
    """Represents a single task in the roadmap."""

    id: str
    description: str
    status: str = "pending"
    attempts: int = 0
    outcomes: list[str] = pydantic.Field(default_factory=list)
    runner: str | None = None
    completed_at: float | None = None
    started_at: float | None = None
    last_started_at: float | None = None
    run_time: float = 0.0
    pid: int | None = None
    last_heartbeat: float | None = None
    has_runner_log: bool = False
    parent: str | None = None
    parent_tasks_file: str | None = None
    completion_requested: bool = False
    failure_requested: bool = False
    index: int | None = pydantic.Field(default=-1, exclude=True)


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


@contextlib.contextmanager
def lock_tasks(tasks_file: pathlib.Path):
    """Context manager for cross-platform file locking.

    Args:
        tasks_file: Path to the tasks YAML file.
    """
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    # Ensure the file exists before we can lock it
    if not tasks_file.exists():
        tasks_file.write_text("{}", encoding="utf-8")

    lock_path = tasks_file.with_suffix(".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def generate_task_id() -> str:
    """Generates a random short hex string for a unique task ID.

    Returns:
        A random 8-character hex string.
    """
    return secrets.token_hex(4)


def is_pid_alive(pid: int) -> bool:
    """Check if a process is still running.

    Args:
        pid: The process ID to check.

    Returns:
        True if the process is alive, False otherwise.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _get_loop_lock_path(tasks_file: pathlib.Path) -> pathlib.Path:
    project_dir = paths.get_project_dir(tasks_file)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / LOOP_LOCK_FILENAME


def acquire_loop_lock(tasks_file: pathlib.Path) -> None:
    """Write a loop lock file with the current PID."""
    _get_loop_lock_path(tasks_file).write_text(str(os.getpid()))


def release_loop_lock(tasks_file: pathlib.Path) -> None:
    """Remove the loop lock file."""
    _get_loop_lock_path(tasks_file).unlink(missing_ok=True)


def is_loop_running(tasks_file: pathlib.Path) -> bool:
    """Check if an orchestrator loop is actively running."""
    pid = get_loop_pid(tasks_file)
    return pid is not None and is_pid_alive(pid)


def get_loop_pid(tasks_file: pathlib.Path) -> int | None:
    """Returns the PID of the running orchestrator loop, if any."""
    lock_path = _get_loop_lock_path(tasks_file)
    if not lock_path.exists():
        return None
    try:
        return int(lock_path.read_text().strip())
    except ValueError, OSError:
        return None


def update_run_time(task: Task, end_time: float | None = None) -> None:
    """Accumulates the execution run time for a given task.

    Args:
        task: The Task to update.
        end_time: Optional end timestamp (defaults to current time).
    """
    if task.last_started_at is not None:
        end = end_time or time.time()
        duration = end - task.last_started_at
        task.run_time += duration
        task.last_started_at = None


def load_tasks(tasks_file: pathlib.Path) -> Roadmap:
    """Loads tasks from a YAML file.

    Args:
        tasks_file: Path to the tasks YAML file.

    Returns:
        A Roadmap containing the context and list of tasks.
    """
    if not tasks_file.exists():
        return Roadmap(
            context="# Project Context\n\nAdd your guidelines here.",
            tasks=[],
        )

    with open(tasks_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        if not data:
            data = {}
        return Roadmap.model_validate(data)


def save_tasks(tasks_file: pathlib.Path, data: Roadmap) -> None:
    """Saves the roadmap data to a YAML file.

    Args:
        tasks_file: Path to the tasks YAML file.
        data: The Roadmap to save.
    """
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    with open(tasks_file, "w", encoding="utf-8") as f:
        yaml.dump(
            data.model_dump(exclude_none=True),
            f,
            default_flow_style=False,
            sort_keys=False,
            width=80,
        )


def get_project_data(tasks_file: pathlib.Path) -> ProjectData:
    """Consolidated logic to get project state (tasks, context, loop status).

    Args:
        tasks_file: Path to the tasks YAML file.

    Returns:
        A ProjectData containing the enriched project state.
    """
    data = load_tasks(tasks_file)
    now = time.time()

    loop_running = is_loop_running(tasks_file)
    for t in data.tasks:
        # Check which log files exist
        t.has_runner_log = paths.get_log_file(tasks_file, t.id).exists()

        # Also detect running tasks as a secondary signal (e.g. external callers)
        if not loop_running and t.status == "in_progress":
            is_stale = False
            if t.last_heartbeat and (now - t.last_heartbeat > STALE_THRESHOLD):
                is_stale = True
            if t.pid and not is_pid_alive(t.pid):
                is_stale = True

            if not is_stale:
                loop_running = True

    # Sort tasks: in_progress, then pending, then completed (most recent first)
    in_progress = [t for t in data.tasks if t.status == "in_progress"]
    pending = [t for t in data.tasks if t.status == "pending"]
    completed = [t for t in data.tasks if t.status == "completed"]
    completed.sort(key=lambda x: x.completed_at or 0)

    sorted_tasks = in_progress + pending + completed

    return ProjectData(
        context=data.context,
        tasks=sorted_tasks,
        config=data.config,
        cwd=str(paths.get_working_dir(tasks_file)),
        loop_running=loop_running,
    )


def get_pending_task(data: Roadmap) -> Task | None:
    """Finds the next task to be executed.

    Args:
        data: The Roadmap to search.

    Returns:
        The next Task to execute, or None if no tasks are pending or a task
        is already in progress.
    """
    now = time.time()
    for task in data.tasks:
        if task.status == "in_progress":
            last_heartbeat = task.last_heartbeat or 0

            # If heartbeat is too old, it's stale
            if now - last_heartbeat > STALE_THRESHOLD:
                return task

            # If we have a PID and it's dead, it's stale
            if task.pid and not is_pid_alive(task.pid):
                return task

            # If it's in progress and not stale, we shouldn't start anything else
            return None

        if task.status == "pending":
            return task

    return None


def _mark_task_in_progress(data: Roadmap, task_id: str, pid: int | None = None) -> bool:
    """Internal helper to mark a task as in_progress without locking or saving.

    Args:
        data: The Roadmap to update.
        task_id: The ID of the task to mark.
        pid: Optional process ID associated with the task.

    Returns:
        True if the task was successfully marked, False otherwise.
    """
    now = time.time()
    for task in data.tasks:
        if task.id == task_id:
            # Check if it's still available (pending or stale)
            is_pending = task.status == "pending"
            is_stale = False
            if task.status == "in_progress":
                last_heartbeat = task.last_heartbeat or 0
                if now - last_heartbeat > STALE_THRESHOLD:
                    is_stale = True
                elif task.pid and not is_pid_alive(task.pid):
                    is_stale = True

            if is_pending or is_stale:
                task.status = "in_progress"
                task.last_heartbeat = now
                if task.started_at is None:
                    task.started_at = now
                task.last_started_at = now
                if pid:
                    task.pid = pid
                return True
            return False
    return False


def mark_task_in_progress(
    tasks_file: pathlib.Path, task_id: str, pid: int | None = None
) -> bool:
    """Try to mark a task as in_progress. Returns True if successful.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to mark.
        pid: Optional process ID associated with the task.

    Returns:
        True if the task was successfully marked, False otherwise.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        if _mark_task_in_progress(data, task_id, pid=pid):
            save_tasks(tasks_file, data)
            return True
    return False


def claim_task(tasks_file: pathlib.Path, task_id: str, pid: int) -> Task | None:
    """Claims a task for execution: marks in_progress and increments attempts.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to claim.
        pid: The process ID of the agent executing the task.

    Returns:
        The claimed Task, or None if the task could not be claimed.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        if not _mark_task_in_progress(data, task_id, pid=pid):
            return None

        task = next(t for t in data.tasks if t.id == task_id)
        task.attempts += 1
        save_tasks(tasks_file, data)
        return task


def finish_task_attempt(tasks_file: pathlib.Path, task_id: str) -> Task | None:
    """Handles post-execution cleanup for a task attempt.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task that finished.

    Returns:
        The updated Task, or None if not found.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if not task:
            return None

        if task.status == "in_progress":
            # Finalize the task based on the runner's request.
            update_run_time(task)

            if task.completion_requested:
                task.status = "completed"
                task.completed_at = time.time()
            else:
                # If it failed or simply finished without requesting completion,
                # we keep it as pending so it can be retried.
                task.status = "pending"

            task.pid = None
            task.last_heartbeat = None
            task.completion_requested = False
            task.failure_requested = False
            save_tasks(tasks_file, data)

        return task


def update_heartbeat(
    tasks_file: pathlib.Path, task_id: str, pid: int | None = None
) -> bool:
    """Updates the heartbeat timestamp for a task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to update.
        pid: Optional new PID to store (e.g. the runner subprocess PID).

    Returns:
        True if the task is still in_progress, False if it was cancelled or
        otherwise changed (caller should stop work).
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        for task in data.tasks:
            if task.id == task_id:
                if task.status != "in_progress":
                    return False
                task.last_heartbeat = time.time()
                if pid is not None:
                    task.pid = pid
                break
        save_tasks(tasks_file, data)
    return True


def reset_task_logs(tasks_file: pathlib.Path, task_id: str) -> None:
    """Deletes the runner log for a given task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task whose logs should be cleared.
    """
    log_file = paths.get_log_file(tasks_file, task_id)
    if log_file.exists():
        log_file.unlink()


def cancel_task(tasks_file: pathlib.Path, task_id: str) -> bool:
    """Kill the process associated with the task AND the orchestrator loop, then mark it as pending.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to cancel.

    Returns:
        True if the task was found and cancellation was attempted, False otherwise.
    """
    import signal

    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        loop_pid = get_loop_pid(tasks_file)

        for task in data.tasks:
            if task.id == task_id:
                # First kill the task itself
                if task.pid:
                    try:
                        # Try to kill the whole process group if possible
                        os.killpg(os.getpgid(task.pid), signal.SIGTERM)
                    except OSError:
                        try:
                            os.kill(task.pid, signal.SIGTERM)
                        except OSError:
                            pass

                # Then kill the loop orchestrator if it's running AND the task was active
                if (
                    task.status == "in_progress"
                    and loop_pid
                    and loop_pid != os.getpid()
                ):
                    try:
                        os.kill(loop_pid, signal.SIGTERM)
                    except OSError:
                        pass

                update_run_time(task)
                task.status = "pending"
                task.pid = None
                task.last_heartbeat = None

                save_tasks(tasks_file, data)
                return True
    return False


def add_task(
    tasks_file: pathlib.Path,
    description: str,
    runner: str | None = None,
    index: int = -1,
    parent: str | None = None,
    parent_tasks_file: str | None = None,
) -> Task:
    """Adds a new task to the roadmap.

    Args:
        tasks_file: Path to the tasks YAML file.
        description: Description of the task.
        runner: Optional preferred runner for this task.
        index: Position to insert the task at (default: append).
        parent: Optional parent task ID.
        parent_tasks_file: Optional parent tasks file path.

    Returns:
        The newly created Task.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        task_id = generate_task_id()
        existing_ids = {t.id for t in data.tasks}
        while task_id in existing_ids:
            task_id = generate_task_id()

        # Detect if we are running inside an agent and set parent automatically
        if not parent:
            parent = os.environ.get("LEMMING_PARENT_TASK_ID")
            if not parent_tasks_file:
                parent_tasks_file = os.environ.get("LEMMING_PARENT_TASKS_FILE")

        new_task = Task(
            id=task_id,
            description=description,
            runner=runner,
            parent=parent,
            parent_tasks_file=parent_tasks_file,
        )

        if index == -1:
            data.tasks.append(new_task)
        else:
            data.tasks.insert(index, new_task)

        save_tasks(tasks_file, data)
    return new_task


def delete_tasks(
    tasks_file: pathlib.Path,
    task_id: str | None = None,
    all_tasks: bool = False,
    completed_only: bool = False,
) -> int:
    """Deletes tasks from the roadmap.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: Optional ID of a specific task to delete.
        all_tasks: If True, deletes all tasks and clears context.
        completed_only: If True, deletes only completed tasks.

    Returns:
        The number of tasks deleted.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        initial_count = len(data.tasks)

        if all_tasks:
            for t in data.tasks:
                reset_task_logs(tasks_file, t.id)
            data.tasks = []
            data.context = ""
        elif completed_only:
            completed_tasks = [t for t in data.tasks if t.status == "completed"]
            for t in completed_tasks:
                reset_task_logs(tasks_file, t.id)
            data.tasks = [t for t in data.tasks if t.status != "completed"]
        elif task_id:
            tasks_to_delete = [t for t in data.tasks if t.id.startswith(task_id)]
            for t in tasks_to_delete:
                reset_task_logs(tasks_file, t.id)
            data.tasks = [t for t in data.tasks if not t.id.startswith(task_id)]

        save_tasks(tasks_file, data)
        return initial_count - len(data.tasks)


def update_task(
    tasks_file: pathlib.Path,
    task_id: str,
    description: str | None = None,
    runner: str | None = None,
    index: int | None = None,
    status: str | None = None,
    require_outcomes: bool = False,
    parent: str | None = None,
    parent_tasks_file: str | None = None,
) -> Task:
    """Updates an existing task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task to update.
        description: New description.
        runner: New preferred runner.
        index: New position in the task list.
        status: New status.
        require_outcomes: If True, raises ValueError if the task has no outcomes.
        parent: New parent task ID.
        parent_tasks_file: New parent tasks file path.
    ...
        if runner is not None:
            target.runner = runner
        if parent_tasks_file is not None:
            if parent_tasks_file == "":
                target.parent_tasks_file = None
            else:
                target.parent_tasks_file = parent_tasks_file
        if parent is not None:

    Raises:
        ValueError: If the task is not found, or if validation fails.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        # Find the task
        task_idx = -1
        target = None
        for i, t in enumerate(data.tasks):
            if t.id.startswith(task_id):
                task_idx = i
                target = t
                break

        if not target:
            raise ValueError(f"Task {task_id} not found")

        if target.status == "completed" and description:
            raise ValueError("Cannot edit description of a completed task")

        if require_outcomes and not target.outcomes:
            raise ValueError(
                f"Task {target.id} has no recorded outcomes. "
                "Record at least one outcome before completing or failing."
            )

        if description is not None:
            target.description = description
        if runner is not None:
            target.runner = runner
        if parent is not None:
            if parent == "":
                target.parent = None
            else:
                target.parent = parent

        if status and status != target.status:
            # If we are running inside the task itself (via an agent), we don't
            # immediately transition to completed/pending. Instead, we set a
            # request flag so the orchestrator loop can run hooks while the
            # task is still 'in_progress' and claimed.
            parent_id = os.environ.get("LEMMING_PARENT_TASK_ID")
            if parent_id == target.id and target.status == "in_progress":
                if status == "completed":
                    target.completion_requested = True
                    target.failure_requested = False
                    save_tasks(tasks_file, data)
                    return target
                if status == "pending":
                    target.failure_requested = True
                    target.completion_requested = False
                    save_tasks(tasks_file, data)
                    return target

            if target.status == "in_progress":
                update_run_time(target)
            if status == "completed":
                target.completed_at = time.time()
            elif status == "pending":
                target.completed_at = None
                target.attempts = 0
            elif target.completed_at is not None:
                target.completed_at = None
            target.status = status
            target.completion_requested = False
            target.failure_requested = False

        if index is not None:
            task_to_move = data.tasks.pop(task_idx)
            if index == -1:
                data.tasks.append(task_to_move)
            else:
                data.tasks.insert(index, task_to_move)

        save_tasks(tasks_file, data)
    return target


def add_outcome(tasks_file: pathlib.Path, task_id: str, text: str) -> Task:
    """Adds an outcome to a task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task.
        text: The outcome text to add.

    Returns:
        The updated Task.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise ValueError(f"Task {task_id} not found")

        if text not in target.outcomes:
            target.outcomes.append(text)
        save_tasks(tasks_file, data)
    return target


def delete_outcome(tasks_file: pathlib.Path, task_id: str, index: int) -> Task:
    """Deletes an outcome from a task by index.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task.
        index: The index of the outcome to delete.

    Returns:
        The updated Task.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise ValueError(f"Task {task_id} not found")

        if index < 0 or index >= len(target.outcomes):
            raise ValueError(
                f"Index {index} out of range (0-{len(target.outcomes) - 1})"
            )

        target.outcomes.pop(index)
        save_tasks(tasks_file, data)
    return target


def edit_outcome(
    tasks_file: pathlib.Path, task_id: str, index: int, new_text: str
) -> Task:
    """Edits an outcome for a task by index.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task.
        index: The index of the outcome to edit.
        new_text: The new outcome text.

    Returns:
        The updated Task.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise ValueError(f"Task {task_id} not found")

        if index < 0 or index >= len(target.outcomes):
            raise ValueError(
                f"Index {index} out of range (0-{len(target.outcomes) - 1})"
            )

        target.outcomes[index] = new_text
        save_tasks(tasks_file, data)
    return target


def reset_task(tasks_file: pathlib.Path, task_id: str) -> Task:
    """Resets a task's attempts and outcomes.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task to reset.

    Returns:
        The reset Task.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise ValueError(f"Task {task_id} not found")

        target.status = "pending"
        target.attempts = 0
        target.outcomes = []
        target.run_time = 0.0
        target.completed_at = None
        target.started_at = None
        target.last_started_at = None
        target.pid = None
        target.last_heartbeat = None
        target.completion_requested = False
        target.failure_requested = False

        save_tasks(tasks_file, data)

        reset_task_logs(tasks_file, target.id)
    return target


def update_context(tasks_file: pathlib.Path, context: str) -> None:
    """Updates the project context.

    Args:
        tasks_file: Path to the tasks YAML file.
        context: The new project context string.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        data.context = context
        save_tasks(tasks_file, data)
