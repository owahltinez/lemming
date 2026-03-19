import contextlib
import os
import pathlib
import secrets
import time
from typing import TypedDict

import yaml
from filelock import FileLock

from . import paths

STALE_THRESHOLD = 30  # seconds


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
    with FileLock(lock_path):
        yield


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


class TaskDict(TypedDict, total=False):
    """Represents a single task in the roadmap."""

    id: str
    description: str
    status: str
    attempts: int
    outcomes: list[str]
    agent: str
    completed_at: float
    started_at: float
    run_time: float
    pid: int
    last_heartbeat: float
    has_log: bool
    tags: list[str]


class RoadmapDict(TypedDict, total=False):
    """Represents the entire roadmap state."""

    context: str
    tasks: list[TaskDict]


class ProjectDataDict(TypedDict, total=False):
    """Represents the project data returned by the API."""

    context: str
    tasks: list[TaskDict]
    cwd: str
    loop_running: bool


def update_run_time(task: TaskDict, end_time: float | None = None) -> None:
    """Accumulates the execution run time for a given task.

    Args:
        task: The TaskDict to update.
        end_time: Optional end timestamp (defaults to current time).
    """
    if "started_at" in task:
        end = end_time or time.time()
        duration = end - task["started_at"]
        task["run_time"] = task.get("run_time", 0) + duration
        del task["started_at"]


def load_tasks(tasks_file: pathlib.Path) -> RoadmapDict:
    """Loads tasks from a YAML file.

    Args:
        tasks_file: Path to the tasks YAML file.

    Returns:
        A RoadmapDict containing the context and list of tasks.
    """
    if not tasks_file.exists():
        return {
            "context": "# Project Context\n\nAdd your guidelines here.",
            "tasks": [],
        }

    with open(tasks_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

        if not data:
            data = {}
        # Ensure schema and migrate lessons to outcomes
        if "context" not in data:
            data["context"] = ""
        if "tasks" not in data:
            data["tasks"] = []
        else:
            for task in data["tasks"]:
                if "lessons" in task:
                    if "outcomes" not in task:
                        task["outcomes"] = []
                    task["outcomes"].extend(task["lessons"])
                    del task["lessons"]
        return data


def save_tasks(tasks_file: pathlib.Path, data: RoadmapDict) -> None:
    """Saves the roadmap data to a YAML file.

    Args:
        tasks_file: Path to the tasks YAML file.
        data: The RoadmapDict to save.
    """
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    with open(tasks_file, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, width=80)


def get_project_data(tasks_file: pathlib.Path) -> ProjectDataDict:
    """Consolidated logic to get project state (tasks, context, loop status).

    Args:
        tasks_file: Path to the tasks YAML file.

    Returns:
        A ProjectDataDict containing the enriched project state.
    """
    data = load_tasks(tasks_file)
    tasks_list = data.get("tasks", [])
    now = time.time()

    loop_running = False
    enriched_tasks: list[TaskDict] = []

    for t in tasks_list:
        # Check if task has a log file
        t["has_log"] = paths.get_log_file(tasks_file, t["id"]).exists()

        # Determine if the loop is running based on this task
        if t.get("status") == "in_progress":
            last_heartbeat = t.get("last_heartbeat")
            pid = t.get("pid")

            is_stale = False
            if last_heartbeat and (now - last_heartbeat > STALE_THRESHOLD):
                is_stale = True
            if pid and not is_pid_alive(pid):
                is_stale = True

            if not is_stale:
                loop_running = True

        enriched_tasks.append(t)

    # Sort tasks: in_progress, then pending, then completed (most recent first)
    in_progress = [t for t in enriched_tasks if t.get("status") == "in_progress"]
    pending = [t for t in enriched_tasks if t.get("status") == "pending"]
    completed = [t for t in enriched_tasks if t.get("status") == "completed"]
    completed.sort(key=lambda x: x.get("completed_at", 0), reverse=True)

    sorted_tasks = in_progress + pending + completed

    return {
        "context": data.get("context", ""),
        "tasks": sorted_tasks,
        "cwd": os.getcwd(),
        "loop_running": loop_running,
    }


def get_pending_task(data: RoadmapDict) -> TaskDict | None:
    """Finds the next task to be executed.

    Args:
        data: The RoadmapDict to search.

    Returns:
        The next TaskDict to execute, or None if no tasks are pending or a task
        is already in progress.
    """
    now = time.time()
    for task in data.get("tasks", []):
        if task.get("status") == "in_progress":
            last_heartbeat = task.get("last_heartbeat", 0)
            pid = task.get("pid")

            # If heartbeat is too old, it's stale
            if now - last_heartbeat > STALE_THRESHOLD:
                return task

            # If we have a PID and it's dead, it's stale
            if pid and not is_pid_alive(pid):
                return task

            # If it's in progress and not stale, we shouldn't start anything else
            return None

        if task.get("status") == "pending":
            return task

    return None


def _mark_task_in_progress(
    data: RoadmapDict, task_id: str, pid: int | None = None
) -> bool:
    """Internal helper to mark a task as in_progress without locking or saving.

    Args:
        data: The RoadmapDict to update.
        task_id: The ID of the task to mark.
        pid: Optional process ID associated with the task.

    Returns:
        True if the task was successfully marked, False otherwise.
    """
    now = time.time()
    for task in data.get("tasks", []):
        if task["id"] == task_id:
            # Check if it's still available (pending or stale)
            is_pending = task.get("status") == "pending"
            is_stale = False
            if task.get("status") == "in_progress":
                last_heartbeat = task.get("last_heartbeat", 0)
                t_pid = task.get("pid")
                if now - last_heartbeat > STALE_THRESHOLD:
                    is_stale = True
                elif t_pid and not is_pid_alive(t_pid):
                    is_stale = True

            if is_pending or is_stale:
                task["status"] = "in_progress"
                task["last_heartbeat"] = now
                task["started_at"] = now
                if pid:
                    task["pid"] = pid
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


def claim_task(tasks_file: pathlib.Path, task_id: str, pid: int) -> TaskDict | None:
    """Claims a task for execution: marks in_progress and increments attempts.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to claim.
        pid: The process ID of the agent executing the task.

    Returns:
        The claimed TaskDict, or None if the task could not be claimed.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        if not _mark_task_in_progress(data, task_id, pid=pid):
            return None

        task = next(t for t in data["tasks"] if t["id"] == task_id)
        task["attempts"] += 1
        save_tasks(tasks_file, data)
        return task


def finish_task_attempt(tasks_file: pathlib.Path, task_id: str) -> TaskDict | None:
    """Handles post-execution cleanup for a task attempt.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task that finished.

    Returns:
        The updated TaskDict, or None if not found.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        task = next((t for t in data["tasks"] if t["id"] == task_id), None)
        if not task:
            return None

        if task.get("status") == "in_progress":
            # Reset to pending if it's still in_progress but the process finished
            update_run_time(task)
            task["status"] = "pending"
            task.pop("pid", None)
            task.pop("last_heartbeat", None)
            save_tasks(tasks_file, data)

        return task


def update_heartbeat(tasks_file: pathlib.Path, task_id: str) -> None:
    """Updates the heartbeat timestamp for a task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to update.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        for task in data.get("tasks", []):
            if task["id"] == task_id:
                task["last_heartbeat"] = time.time()
                break
        save_tasks(tasks_file, data)


def clear_log(tasks_file: pathlib.Path, task_id: str) -> None:
    """Deletes the log file for a given task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task whose log should be cleared.
    """
    log_file = paths.get_log_file(tasks_file, task_id)
    if log_file.exists():
        log_file.unlink()


def cancel_task(tasks_file: pathlib.Path, task_id: str) -> bool:
    """Kill the process associated with the task and mark it as pending.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to cancel.

    Returns:
        True if the task was found and cancellation was attempted, False otherwise.
    """
    import signal

    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        for task in data.get("tasks", []):
            if task["id"] == task_id:
                pid = task.get("pid")
                if pid:
                    try:
                        # Try to kill the whole process group if possible
                        os.killpg(os.getpgid(pid), signal.SIGTERM)
                    except OSError:
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except OSError:
                            pass

                update_run_time(task)
                task["status"] = "pending"
                if "pid" in task:
                    del task["pid"]
                if "last_heartbeat" in task:
                    del task["last_heartbeat"]

                save_tasks(tasks_file, data)
                return True
    return False


def add_task(
    tasks_file: pathlib.Path,
    description: str,
    agent: str | None = None,
    index: int = -1,
    tags: list[str] | None = None,
) -> TaskDict:
    """Adds a new task to the roadmap.

    Args:
        tasks_file: Path to the tasks YAML file.
        description: Description of the task.
        agent: Optional preferred agent for this task.
        index: Position to insert the task at (default: append).
        tags: Optional list of tags to add to the task.

    Returns:
        The newly created TaskDict.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        task_id = generate_task_id()
        existing_ids = {t["id"] for t in data["tasks"]}
        while task_id in existing_ids:
            task_id = generate_task_id()

        # Detect if we are running inside an agent and tag automatically
        all_tags = set(tags or [])
        parent_id = os.environ.get("LEMMING_PARENT_TASK_ID")
        if parent_id:
            all_tags.add("agent")
            all_tags.add(f"parent:{parent_id}")

        new_task: TaskDict = {
            "id": task_id,
            "description": description,
            "status": "pending",
            "attempts": 0,
            "outcomes": [],
            "tags": sorted(list(all_tags)),
        }
        if agent:
            new_task["agent"] = agent

        if index == -1:
            data["tasks"].append(new_task)
        else:
            data["tasks"].insert(index, new_task)

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
        initial_count = len(data["tasks"])

        if all_tasks:
            for t in data["tasks"]:
                clear_log(tasks_file, t["id"])
            data["tasks"] = []
            data["context"] = ""
        elif completed_only:
            completed_tasks = [
                t for t in data["tasks"] if t.get("status") == "completed"
            ]
            for t in completed_tasks:
                clear_log(tasks_file, t["id"])
            data["tasks"] = [t for t in data["tasks"] if t.get("status") != "completed"]
        elif task_id:
            tasks_to_delete = [t for t in data["tasks"] if t["id"].startswith(task_id)]
            for t in tasks_to_delete:
                clear_log(tasks_file, t["id"])
            data["tasks"] = [
                t for t in data["tasks"] if not t["id"].startswith(task_id)
            ]

        save_tasks(tasks_file, data)
        return initial_count - len(data["tasks"])


def update_task(
    tasks_file: pathlib.Path,
    task_id: str,
    description: str | None = None,
    agent: str | None = None,
    index: int | None = None,
    status: str | None = None,
    require_outcomes: bool = False,
    tags: list[str] | None = None,
) -> TaskDict:
    """Updates an existing task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task to update.
        description: New description.
        agent: New preferred agent.
        index: New position in the task list.
        status: New status.
        require_outcomes: If True, raises ValueError if the task has no outcomes.
        tags: New tags for the task.

    Returns:
        The updated TaskDict.

    Raises:
        ValueError: If the task is not found, or if validation fails.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        # Find the task
        task_idx = -1
        target = None
        for i, t in enumerate(data["tasks"]):
            if t["id"].startswith(task_id):
                task_idx = i
                target = t
                break

        if not target:
            raise ValueError(f"Task {task_id} not found")

        if target.get("status") == "completed" and description:
            raise ValueError("Cannot edit description of a completed task")

        if require_outcomes and not target.get("outcomes"):
            raise ValueError(
                f"Task {target['id']} has no recorded outcomes. "
                "Record at least one outcome before completing or failing."
            )

        if description is not None:
            target["description"] = description
        if agent is not None:
            target["agent"] = agent
        if tags is not None:
            target["tags"] = sorted(list(set(tags)))

        if status and status != target.get("status"):
            if target.get("status") == "in_progress":
                update_run_time(target)
            if status == "completed":
                target["completed_at"] = time.time()
            elif status == "pending":
                target.pop("completed_at", None)
                target["attempts"] = 0
            elif "completed_at" in target:
                del target["completed_at"]
            target["status"] = status

        if index is not None:
            task_to_move = data["tasks"].pop(task_idx)
            if index == -1:
                data["tasks"].append(task_to_move)
            else:
                data["tasks"].insert(index, task_to_move)

        save_tasks(tasks_file, data)
    return target


def add_outcome(tasks_file: pathlib.Path, task_id: str, text: str) -> TaskDict:
    """Adds an outcome to a task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task.
        text: The outcome text to add.

    Returns:
        The updated TaskDict.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)
        if not target:
            raise ValueError(f"Task {task_id} not found")

        if "outcomes" not in target:
            target["outcomes"] = []
        target["outcomes"].append(text)
        save_tasks(tasks_file, data)
    return target


def reset_task(tasks_file: pathlib.Path, task_id: str) -> TaskDict:
    """Resets a task's attempts and outcomes.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task to reset.

    Returns:
        The reset TaskDict.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)
        if not target:
            raise ValueError(f"Task {task_id} not found")

        target["status"] = "pending"
        target["attempts"] = 0
        target["outcomes"] = []
        target["run_time"] = 0
        target.pop("completed_at", None)
        target.pop("started_at", None)
        target.pop("pid", None)
        target.pop("last_heartbeat", None)

        save_tasks(tasks_file, data)
        clear_log(tasks_file, target["id"])
    return target


def update_context(tasks_file: pathlib.Path, context: str) -> None:
    """Updates the project context.

    Args:
        tasks_file: Path to the tasks YAML file.
        context: The new project context string.
    """
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        data["context"] = context
        save_tasks(tasks_file, data)
