from __future__ import annotations

import contextlib
import os
import pathlib
import secrets
import subprocess
import time
from typing import TYPE_CHECKING

from filelock import FileLock

if TYPE_CHECKING:
    from . import tasks

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


def update_run_time(task: tasks.TaskDict, end_time: float | None = None) -> None:
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


def in_git_repo() -> bool:
    """Check if the current directory is inside a git repository.

    The result is cached on the function after the first call.

    Returns:
        True if inside a git repository, False otherwise.
    """
    if not hasattr(in_git_repo, "_result"):
        try:
            in_git_repo._result = (
                subprocess.run(
                    ["git", "rev-parse", "--git-dir"],
                    capture_output=True,
                ).returncode
                == 0
            )
        except Exception:
            in_git_repo._result = False
    return in_git_repo._result


def is_ignored(path: pathlib.Path) -> bool:
    """Check if a given path is ignored by git.

    Args:
        path: The path to check for git-ignore status.

    Returns:
        True if the path is ignored by git, False otherwise.
    """
    if not in_git_repo():
        return False
    try:
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", str(path)],
                capture_output=True,
            ).returncode
            == 0
        )
    except Exception:
        return False
