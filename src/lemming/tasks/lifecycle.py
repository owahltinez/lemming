"""Task lifecycle management: claiming, heartbeats, cancellation, resets."""

import logging
import os
import pathlib
import secrets
import signal
import subprocess
import time

from .. import models, paths, persistence, snapshots
from . import limits, state_machine

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
    """Returns True if a live, heartbeating owner is working on the task."""
    if task.status not in models.ACTIVE_STATUSES:
        return False

    if not task.pid or not is_pid_alive(task.pid):
        return False

    last_heartbeat = task.last_heartbeat or 0
    return now - last_heartbeat <= persistence.STALE_THRESHOLD


def claim_task(
    tasks_file: pathlib.Path,
    task_id: str,
    pid: int,
    working_dir: pathlib.Path | None = None,
    status: models.TaskStatus = models.TaskStatus.RUNNING,
) -> models.Task | None:
    """Gives a task to an owner: starts a pending one or resumes a review.

    Starting a run counts an attempt and checkpoints the workspace as its
    pre_tree. A review left unowned is resumed as it was.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: The ID of the task to claim.
        pid: The process ID of the new owner.
        working_dir: Workspace to checkpoint; defaults to the tasks file's.
        status: What a pending task moves to: running, or failing once it
            has run out of attempts.

    Returns:
        The claimed Task, or None if the task could not be claimed.
    """
    pre_tree = pre_head = None
    if status == models.TaskStatus.RUNNING:
        working_dir = working_dir or paths.get_working_dir(tasks_file)
        pre_tree = snapshots.snapshot_tree(tasks_file, working_dir)
        pre_head = snapshots.head_commit(working_dir)
    now = time.time()
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if task is None or is_task_active(task, now):
            return None
        if task.status == models.TaskStatus.PENDING:
            state_machine.set_status(task, status, now)
            if status == models.TaskStatus.RUNNING:
                task.pre_tree = pre_tree
                task.pre_head = pre_head
                task.runner_tree = None
                task.hook_trees = None
        elif task.status in (
            models.TaskStatus.COMPLETING,
            models.TaskStatus.FAILING,
        ):
            task.last_started_at = now
            task.last_heartbeat = now
        else:
            return None
        task.pid = pid
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
        if target.status != models.TaskStatus.COMPLETING:
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
        True if the task is still active, False if it was cancelled or
        otherwise changed (caller should stop work).
    """
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        for task in data.tasks:
            if task.id == task_id:
                if task.status not in models.ACTIVE_STATUSES:
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


def escaped_descendants(pid: int, pgid: int) -> list[int]:
    """Returns the descendants of a process that left its process group.

    Killing the group misses them, and once their parent dies they reparent
    to init and can no longer be found, so collect them before killing.

    Args:
        pid: The process whose descendants to list.
        pgid: The process group the descendants left.

    Returns:
        The PIDs of descendants outside ``pgid``; empty if ``ps`` fails.
    """
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=persistence.PS_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            children.setdefault(int(fields[1]), []).append(int(fields[0]))

    descendants: set[int] = set()
    stack = [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in descendants and child not in (0, 1, os.getpid()):
                descendants.add(child)
                stack.append(child)

    escaped = []
    for child in descendants:
        try:
            if os.getpgid(child) != pgid:
                escaped.append(child)
        except OSError:
            pass
    return escaped


def signal_pids(pids: list[int], sig: int) -> None:
    """Sends a signal to each process, skipping any that are gone."""
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def kill_survivors(pids: list[int], deadline: float) -> None:
    """Waits for processes to exit, SIGKILLing any alive at the deadline.

    Args:
        pids: The processes already sent SIGTERM.
        deadline: The ``time.monotonic()`` value ending the grace period.
    """
    pids = [pid for pid in pids if is_pid_alive(pid)]
    while pids and time.monotonic() < deadline:
        time.sleep(0.1)
        pids = [pid for pid in pids if is_pid_alive(pid)]
    signal_pids(pids, signal.SIGKILL)


def _kill_pid_tree(pid: int) -> None:
    """Kills a process, its entire process group, and escaped descendants.

    Sends SIGTERM to the process group first so it can clean up, then
    escalates to SIGKILL if the process is still alive after a grace
    period. Descendants that left the group are terminated the same way.
    Unlike runner._kill_process_tree, this works from a bare PID (no
    subprocess handle), so exit is detected by polling.

    Args:
        pid: The process ID to kill.
    """
    # Ask the whole group to terminate gracefully.
    pgid = None
    escaped: list[int] = []
    try:
        pgid = os.getpgid(pid)
        escaped = escaped_descendants(pid, pgid)
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    signal_pids(escaped, signal.SIGTERM)

    # Poll for exit, then escalate to SIGKILL if SIGTERM was ignored.
    deadline = time.monotonic() + KILL_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            kill_survivors(escaped, deadline)
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
    kill_survivors(escaped, deadline)


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
            an active task.

    Returns:
        True if the task was found and cancellation was attempted, False
        otherwise.

    Raises:
        state_machine.TransitionError: If the task has already finished.
    """
    # Update the task state first and release the lock: the kill below can
    # wait out a grace period and must not stall heartbeats or readers.
    task_pid = None
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
        if (
            task.status in models.ACTIVE_STATUSES
            and task.pid != persistence.get_loop_pid(tasks_file)
        ):
            task_pid = task.pid
        if task.status not in models.ACTIVE_STATUSES:
            # Only an attempt in flight has changes of its own to undo.
            task.pre_tree = None
            task.pre_head = None
        state_machine.set_status(task, models.TaskStatus.CANCELLED)
        persistence.save_tasks(tasks_file, data)

    if task_pid:
        _kill_pid_tree(task_pid)

    state_machine.settle(
        tasks_file,
        resolved_id,
        working_dir or paths.get_working_dir(tasks_file),
    )
    return True


def clear_task_state(task: models.Task) -> None:
    """Resets a task's status, attempts, progress, and snapshot fields."""
    state_machine.set_status(task, models.TaskStatus.PENDING)
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

        was_active = target.status in models.ACTIVE_STATUSES
        clear_task_state(target)
        persistence.save_tasks(tasks_file, data)
        if not was_active and not is_loop_lock_held(tasks_file):
            snapshots.prune_snapshots(
                tasks_file, snapshots.collect_task_trees(data.tasks)
            )

        reset_task_logs(tasks_file, target.id)
    return target
