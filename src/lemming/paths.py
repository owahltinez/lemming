import hashlib
import os
import pathlib


def get_lemming_home() -> pathlib.Path:
    """Returns the lemming home directory."""
    home_override = os.environ.get("LEMMING_HOME")
    if home_override:
        return pathlib.Path(home_override)
    return pathlib.Path.home() / ".local" / "lemming"


def get_project_dir(tasks_file: pathlib.Path) -> pathlib.Path:
    """Returns the project directory for a given tasks file."""
    tasks_file_abs = tasks_file.resolve()
    lemming_home = get_lemming_home()

    # If the tasks file is already inside lemming home, its parent IS the project dir
    if tasks_file_abs.parent.parent == lemming_home:
        return tasks_file_abs.parent

    # Otherwise, hash the absolute path to get a unique project dir
    path_hash = hashlib.sha256(str(tasks_file_abs).encode()).hexdigest()[:12]
    return lemming_home / path_hash


def get_default_tasks_file() -> pathlib.Path:
    """Determine the default tasks file location."""
    local_tasks = pathlib.Path("tasks.yml")
    if local_tasks.exists():
        return local_tasks

    # If no local tasks.yml, create a subdirectory under ~/.local/lemming/
    # using a hash of the current working directory path.
    cwd_path = str(pathlib.Path.cwd().resolve())
    path_hash = hashlib.sha256(cwd_path.encode()).hexdigest()[:12]

    return get_lemming_home() / path_hash / "tasks.yml"


def get_log_file(tasks_file: pathlib.Path, task_id: str) -> pathlib.Path:
    """Returns the log file path for a given task."""
    project_dir = get_project_dir(tasks_file)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / f"{task_id}.log"
