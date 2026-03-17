import contextlib
import fcntl
import os
import pathlib
import secrets
import time

STALE_THRESHOLD = 30  # seconds


@contextlib.contextmanager
def lock_tasks(tasks_file: pathlib.Path):
    """Context manager for file locking."""
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    # Ensure the file exists before we can lock it
    if not tasks_file.exists():
        tasks_file.write_text("{}", encoding="utf-8")

    with open(tasks_file, "r+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield f
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def generate_task_id() -> str:
    """Generates a random short hex string for the task ID."""
    return secrets.token_hex(4)


def is_pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def update_run_time(task: dict, end_time: float | None = None) -> None:
    """Accumulate run time for the task."""
    if "started_at" in task:
        end = end_time or time.time()
        duration = end - task["started_at"]
        task["run_time"] = task.get("run_time", 0) + duration
        del task["started_at"]


def in_git_repo() -> bool:
    """Check if cwd is inside a git repository (cached after first call)."""
    import subprocess

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
    """Check if a path is ignored by git."""
    import subprocess

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
