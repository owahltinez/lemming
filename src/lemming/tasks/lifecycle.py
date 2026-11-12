"""Task lifecycle management: claiming, heartbeats, cancellation, resets."""

import logging
import os
import pathlib
import secrets
import signal
import time

from .. import models, paths, persistence, snapshots
from . import limits

logger = logging.getLogger(__name__)

# Grace period after SIGTERM before escalating to SIGKILL.
KILL_GRACE_SECONDS = 5


def generate_task_id() -> str:
    """Generates a random short hex string for a unique task ID.

    Returns:
        A random 8-character hex string.
    """
    return secrets.token_hex(4)


def is_pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
    return persistence.is_pid_alive(pid)


def is_loop_running(tasks_file: pathlib.Path) -> bool:
    """Check if an orchestrator loop is actively running."""
    return persistence.is_loop_running(tasks_file)


def is_loop_lock_held(tasks_file: pathlib.Path) -> bool:
    """Check if a live process holds the loop lock while holding tasks.lock."""
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


def record_execution_time(
    tasks_file: pathlib.Path,
    task_id: str,
    component: str,
    duration: float,
) -> None:
    """Adds subprocess runtime to a task's per-component breakdown.

    Missing tasks are ignored because a roadmap hook may legitimately delete
    or replace the task whose runner just exited.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task whose subprocess ran.
        component: Stable component key, such as ``runner`` or
            ``hook:readability``.
        duration: Elapsed subprocess time in seconds.
    """
    if duration < 0:
        return

    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if task is None:
            return

        if task.execution_times is None:
            task.execution_times = {}
        task.execution_times[component] = (
            task.execution_times.get(component, 0.0) + duration
        )
        if task.active_execution_component == component:
            task.active_execution_component = None
            task.active_execution_started_at = None
        persistence.save_tasks(tasks_file, data)


def record_resolved_command(
    tasks_file: pathlib.Path,
    task_id: str,
    command: str,
) -> None:
    """Records the runner command an attempt actually launched.

    Without this the runner and model behind a finished task are only
    recoverable by inspecting the live subprocess, which is impossible after
    the fact.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task being executed.
        command: Resolved command with the prompt elided.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if task is None:
            return

        task.resolved_command = command
        persistence.save_tasks(tasks_file, data)


def mark_execution_started(
    tasks_file: pathlib.Path,
    task_id: str,
    component: str,
    started_at: float | None = None,
) -> None:
    """Records the component whose elapsed time is currently accumulating.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task whose subprocess started.
        component: Stable component key, such as ``runner``.
        started_at: Optional wall-clock timestamp, defaulting to now.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if task is None:
            return

        task.active_execution_component = component
        task.active_execution_started_at = started_at or time.time()
        persistence.save_tasks(tasks_file, data)


def is_task_active(task: models.Task, now: float) -> bool:
    """Returns True if the task is actively being executed.

    A task is active if:
    1. It is marked IN_PROGRESS or has a requested_status (finalizing).
    2. AND it has a PID that is alive.
    3. AND its heartbeat is recent (not stale).
    """
    if (
        task.status != models.TaskStatus.IN_PROGRESS
        and not task.requested_status
    ):
        return False

    if not task.pid or not is_pid_alive(task.pid):
        return False

    last_heartbeat = task.last_heartbeat or 0
    if now - last_heartbeat > persistence.STALE_THRESHOLD:
        return False

    return True


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
            # A task can be marked in progress if it's pending, or if it's
            # nominally in progress/finalizing but has crashed or timed out
            # (stale).
            if task.status == models.TaskStatus.PENDING or (
                (
                    task.status == models.TaskStatus.IN_PROGRESS
                    or task.requested_status
                )
                and not is_task_active(task, now)
            ):
                task.status = models.TaskStatus.IN_PROGRESS
                # A new attempt must not inherit the previous one's
                # rejection, which would block its completion too.
                task.rejection = None
                task.last_heartbeat = now
                if task.started_at is None:
                    task.started_at = now
                task.last_started_at = now
                task.active_execution_component = None
                task.active_execution_started_at = None
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


def claim_task(
    tasks_file: pathlib.Path, task_id: str, pid: int
) -> models.Task | None:
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
        if not task.requested_status:
            task.attempts += 1
        persistence.save_tasks(tasks_file, data)
        return task


def finish_task_attempt(
    tasks_file: pathlib.Path,
    task_id: str,
    count_attempt: bool = True,
) -> models.Task | None:
    """Handles post-execution cleanup for a task attempt.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task that finished.
        count_attempt: Whether the attempt should consume the retry budget.

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
                if not count_attempt:
                    task.attempts = max(0, task.attempts - 1)
            else:
                task.last_heartbeat = time.time()

            task.pid = None
            persistence.save_tasks(tasks_file, data)

        return task


def revert_task_to_pending(
    tasks_file: pathlib.Path, task_id: str
) -> models.Task | None:
    """Reverts an in-progress or finalizing task to pending for a retry.

    Unlike reset_task, this preserves the attempt count and progress so
    retry accounting still applies.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to revert.

    Returns:
        The updated Task, or None if not found.
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if not task:
            return None

        update_run_time(task)
        task.status = models.TaskStatus.PENDING
        task.requested_status = None
        task.rejection = None
        task.pid = None
        task.last_heartbeat = None
        task.active_execution_component = None
        task.active_execution_started_at = None

        persistence.save_tasks(tasks_file, data)
        return task


def reject_task(
    tasks_file: pathlib.Path, task_id: str, reason: str
) -> models.Task:
    """Records a finalization hook's objection to a requested completion.

    The status is deliberately left alone so the remaining hooks still run
    and see the objection; the orchestrator folds it into finalization by
    reverting the task to pending once they are done.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task to reject, or a prefix of one.
        reason: Why the work is not acceptable as complete.

    Returns:
        The rejected Task.

    Raises:
        ValueError: If the reason is empty or oversized, the task is missing,
            or the task is not finalizing toward completion.
    """
    reason = reason.strip()
    if not reason:
        raise ValueError("Rejection reason cannot be empty")

    # The reason is replayed as a progress entry, so it lives under the same
    # bound; exceeding it would fail later, halfway through finalization.
    limits.validate_progress_entry(reason)

    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            raise ValueError(f"Task {task_id} not found")

        # Only a completion that has not been applied yet can be blocked.
        if (
            target.status != models.TaskStatus.IN_PROGRESS
            or target.requested_status != models.TaskStatus.COMPLETED
        ):
            raise ValueError(
                f"Task {target.id} is not awaiting completion; only a task "
                "finalizing as completed can be rejected."
            )

        target.rejection = reason
        persistence.save_tasks(tasks_file, data)
    return target


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


def write_patch_artifact(
    tasks_file: pathlib.Path,
    task_id: str,
    filename: str,
    pre_tree: str | None,
    post_tree: str | None,
) -> None:
    """Writes the diff between two trees into the task's artifacts directory."""
    if not pre_tree or not post_tree or pre_tree == post_tree:
        return
    patch_text = snapshots.diff_trees(tasks_file, pre_tree, post_tree)
    if not patch_text:
        return
    artifacts_dir = paths.get_artifacts_dir(tasks_file, task_id)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / filename).write_text(
        patch_text, encoding="utf-8", errors="surrogateescape"
    )


def _kill_pid_tree(pid: int) -> None:
    """Kills a process and its entire process group by PID.

    Sends SIGTERM to the process group first so it can clean up, then
    escalates to SIGKILL if the process is still alive after a grace
    period. Unlike runner._kill_process_tree, this works from a bare PID
    (no subprocess handle), so exit is detected by polling.

    Args:
        pid: The process ID to kill.
    """
    # Ask the whole group to terminate gracefully.
    pgid = None
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return

    # Poll for exit, then escalate to SIGKILL if SIGTERM was ignored.
    deadline = time.monotonic() + KILL_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return
        time.sleep(0.1)

    logger.warning(
        "Process %d survived SIGTERM for %ss; sending SIGKILL.",
        pid,
        KILL_GRACE_SECONDS,
    )
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def cancel_task(
    tasks_file: pathlib.Path,
    task_id: str,
    working_dir: pathlib.Path | None = None,
) -> bool:
    """Mark a task cancelled and stop its runner process, if active.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to cancel.
        working_dir: Optional workspace directory to restore when cancelling
            an in-progress task.

    Returns:
        True if the task was found and cancellation was attempted, False
        otherwise.
    """
    # Update the task state first and release the lock: the kill below can
    # wait out a grace period and must not stall heartbeats or readers.
    task_pid = None
    was_in_progress = False
    pre_tree = None
    prev_runner_tree = None
    resolved_id = task_id
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        task = next(
            (t for t in data.tasks if t.id == task_id),
            None,
        ) or next(
            (t for t in data.tasks if t.id.startswith(task_id)),
            None,
        )
        if not task:
            return False

        resolved_id = task.id
        was_in_progress = task.status == models.TaskStatus.IN_PROGRESS
        pre_tree = task.pre_tree
        prev_runner_tree = task.runner_tree
        loop_pid = persistence.get_loop_pid(tasks_file)
        if was_in_progress and task.pid != loop_pid:
            task_pid = task.pid

        update_run_time(task)
        task.status = models.TaskStatus.CANCELLED
        task.pid = None
        task.last_heartbeat = None
        task.requested_status = None
        task.active_execution_component = None
        task.active_execution_started_at = None
        task.runner_tree = None
        task.hook_trees = None
        if not ((was_in_progress or prev_runner_tree is not None) and pre_tree):
            task.pre_tree = None
            task.pre_head = None

        persistence.save_tasks(tasks_file, data)

    if task_pid:
        _kill_pid_tree(task_pid)

    if (was_in_progress or prev_runner_tree is not None) and pre_tree:
        resolved_dir = working_dir or paths.get_working_dir(tasks_file)
        with persistence.lock_tasks(tasks_file):
            data = persistence.load_tasks(tasks_file)
            current = next((t for t in data.tasks if t.id == resolved_id), None)
            if current is not None and current.pre_tree is not None:
                restore_target = current.pre_tree
                restore_head = current.pre_head
                current_tree = snapshots.snapshot_tree(tasks_file, resolved_dir)
                write_patch_artifact(
                    tasks_file,
                    resolved_id,
                    "cancelled.patch",
                    restore_target,
                    current_tree,
                )
                if current_tree != restore_target or (
                    restore_head is not None
                    and snapshots.head_commit(resolved_dir) != restore_head
                ):
                    snapshots.restore_tree(
                        tasks_file,
                        resolved_dir,
                        restore_target,
                        current_tree=current_tree,
                        target_head=restore_head,
                    )
                current.pre_tree = None
                current.pre_head = None
                current.runner_tree = None
                current.hook_trees = None
                persistence.save_tasks(tasks_file, data)

    return True


def clear_task_state(task: models.Task) -> None:
    """Resets a task's status, attempts, progress, and snapshot fields."""
    task.status = models.TaskStatus.PENDING
    task.attempts = 0
    task.progress = []
    task.run_time = 0.0
    task.execution_times = None
    task.active_execution_component = None
    task.active_execution_started_at = None
    task.completed_at = None
    task.superseded_at = None
    task.superseded_reason = None
    task.started_at = None
    task.last_started_at = None
    task.pid = None
    task.last_heartbeat = None
    task.requested_status = None
    task.rejection = None
    task.pre_tree = None
    task.runner_tree = None
    task.post_tree = None
    task.pre_head = None
    task.diff_summary = None
    task.hook_trees = None


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

        was_in_progress = target.status == models.TaskStatus.IN_PROGRESS
        clear_task_state(target)
        persistence.save_tasks(tasks_file, data)
        if not was_in_progress and not is_loop_lock_held(tasks_file):
            snapshots.prune_snapshots(
                tasks_file, snapshots.collect_task_trees(data.tasks)
            )

        reset_task_logs(tasks_file, target.id)
    return target
