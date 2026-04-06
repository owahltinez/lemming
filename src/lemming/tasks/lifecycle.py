import os
import pathlib
import secrets
import time

from .. import models, paths, persistence

# Re-export constants for internal/external use
STALE_THRESHOLD = persistence.STALE_THRESHOLD


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

    # Check for zombie state on Linux
    try:
        status_path = pathlib.Path(f"/proc/{pid}/status")
        if status_path.exists():
            for line in status_path.read_text().splitlines():
                if line.startswith("State:"):
                    state = line.split()[1]
                    if state == "Z":
                        return False
    except OSError:
        pass

    return True


def is_loop_running(tasks_file: pathlib.Path) -> bool:
    """Check if an orchestrator loop is actively running."""
    pid = persistence.get_loop_pid(tasks_file)
    return pid is not None and is_pid_alive(pid)


def update_run_time(task: models.Task, end_time: float | None = None) -> None:
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


def _mark_task_in_progress(
    data: models.Roadmap, task_id: str, pid: int | None = None
) -> bool:
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
            is_pending = task.status == models.TaskStatus.PENDING
            is_stale = False
            if task.status == models.TaskStatus.IN_PROGRESS:
                last_heartbeat = task.last_heartbeat or 0
                if now - last_heartbeat > STALE_THRESHOLD:
                    is_stale = True
                elif task.pid and not is_pid_alive(task.pid):
                    is_stale = True

            if is_pending or is_stale:
                task.status = models.TaskStatus.IN_PROGRESS
                task.last_heartbeat = now
                if task.started_at is None:
                    task.started_at = now
                task.last_started_at = now
                if pid is not None:
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
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        if _mark_task_in_progress(data, task_id, pid=pid):
            persistence.save_tasks(tasks_file, data)
            return True
    return False


def claim_task(tasks_file: pathlib.Path, task_id: str, pid: int) -> models.Task | None:
    """Claims a task for execution: marks in_progress and increments attempts.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to claim.
        pid: The process ID of the agent executing the task.

    Returns:
        The claimed Task, or None if the task could not be claimed.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        if not _mark_task_in_progress(data, task_id, pid=pid):
            return None

        task = next(t for t in data.tasks if t.id == task_id)
        task.attempts += 1
        persistence.save_tasks(tasks_file, data)
        return task


def finish_task_attempt(tasks_file: pathlib.Path, task_id: str) -> models.Task | None:
    """Handles post-execution cleanup for a task attempt.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task that finished.

    Returns:
        The updated Task, or None if not found.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if not task:
            return None

        if task.status == models.TaskStatus.IN_PROGRESS:
            # If it failed or simply finished without requesting completion,
            # we keep it as pending so it can be retried.
            if not task.requested_status:
                update_run_time(task)
                task.status = models.TaskStatus.PENDING
                task.last_heartbeat = None
            else:
                task.last_heartbeat = time.time()

            task.pid = None
            persistence.save_tasks(tasks_file, data)

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
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        for task in data.tasks:
            if task.id == task_id:
                if task.status != models.TaskStatus.IN_PROGRESS:
                    return False
                task.last_heartbeat = time.time()
                if pid is not None:
                    task.pid = pid
                break
        persistence.save_tasks(tasks_file, data)
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
    """Kill the process associated with the task AND the orchestrator loop, then mark it as cancelled.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to cancel.

    Returns:
        True if the task was found and cancellation was attempted, False otherwise.
    """
    import signal

    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)

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

                # Also kill the orchestrator loop if it's running
                loop_pid = persistence.get_loop_pid(tasks_file)
                if loop_pid:
                    try:
                        os.kill(loop_pid, signal.SIGTERM)
                    except OSError:
                        pass

                update_run_time(task)
                task.status = models.TaskStatus.CANCELLED
                task.pid = None
                task.last_heartbeat = None
                task.requested_status = None

                persistence.save_tasks(tasks_file, data)
                return True
    return False


def reset_task(tasks_file: pathlib.Path, task_id: str) -> models.Task:
    """Resets a task's attempts and progress.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task to reset.

    Returns:
        The reset Task.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise models.TaskNotFoundError(f"Task {task_id} not found")

        target.status = models.TaskStatus.PENDING
        target.attempts = 0
        target.progress = []
        target.run_time = 0.0
        target.completed_at = None
        target.started_at = None
        target.last_started_at = None
        target.pid = None
        target.last_heartbeat = None
        target.requested_status = None

        persistence.save_tasks(tasks_file, data)

        reset_task_logs(tasks_file, target.id)
    return target
