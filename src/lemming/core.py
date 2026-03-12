import hashlib
import pathlib
import secrets
import time
import yaml
import fcntl
import contextlib

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


def get_default_tasks_file() -> pathlib.Path:
    """Determine the default tasks file location."""
    local_tasks = pathlib.Path("tasks.yml")
    if local_tasks.exists():
        return local_tasks

    # If no local tasks.yml, create a subdirectory under ~/.local/lemming/
    # using a hash of the current working directory path.
    cwd_path = str(pathlib.Path.cwd().resolve())
    path_hash = hashlib.sha256(cwd_path.encode()).hexdigest()[:12]

    return (
        pathlib.Path.home()
        / ".local"
        / "lemming"
        / "projects"
        / path_hash
        / "tasks.yml"
    )


def load_prompt(name: str) -> str:
    """Loads a prompt template from the prompts directory."""
    base_path = pathlib.Path(__file__).parent / "prompts"
    prompt_path = base_path / f"{name}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template {name} not found at {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def generate_task_id() -> str:
    """Generates a random short hex string for the task ID."""
    return secrets.token_hex(4)


def load_tasks(tasks_file: pathlib.Path) -> dict:
    if not tasks_file.exists():
        return {
            "context": "# Project Context\n\nAdd your guidelines here.",
            "tasks": [],
        }

    # We don't lock here because many places just read,
    # and we want to allow concurrent reads if possible.
    # But for state-changing operations, we should use a lock.
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


def save_tasks(tasks_file: pathlib.Path, data: dict) -> None:
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    with open(tasks_file, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, width=80)


def is_pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
    import os

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def get_pending_task(data: dict) -> dict | None:
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


def mark_task_in_progress(
    tasks_file: pathlib.Path, task_id: str, pid: int | None = None
) -> bool:
    """Try to mark a task as in_progress. Returns True if successful."""
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
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
                    save_tasks(tasks_file, data)
                    return True
                return False
    return False


def update_run_time(task: dict, end_time: float | None = None) -> None:
    """Accumulate run time for the task."""
    if "started_at" in task:
        end = end_time or time.time()
        duration = end - task["started_at"]
        task["run_time"] = task.get("run_time", 0) + duration
        del task["started_at"]


def update_heartbeat(tasks_file: pathlib.Path, task_id: str) -> None:
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)
        for task in data.get("tasks", []):
            if task["id"] == task_id:
                task["last_heartbeat"] = time.time()
                break
        save_tasks(tasks_file, data)


def cancel_task(tasks_file: pathlib.Path, task_id: str) -> bool:
    """Kill the process associated with the task and mark it as pending."""
    import os
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
