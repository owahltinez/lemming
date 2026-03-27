import hashlib
import os
import pathlib
import subprocess


def get_lemming_home() -> pathlib.Path:
    """Determines the Lemming home directory from the environment or default.

    Returns:
        A pathlib.Path representing the Lemming home directory.
    """
    home_override = os.environ.get("LEMMING_HOME")
    if home_override:
        return pathlib.Path(home_override)
    return pathlib.Path.home() / ".local" / "lemming"


def get_project_dir(tasks_file: pathlib.Path) -> pathlib.Path:
    """Determines the isolated project directory for a given tasks file.

    Args:
        tasks_file: Path to the tasks YAML file.

    Returns:
        A pathlib.Path to the isolated directory where project logs and state
        should be stored.
    """
    tasks_file_abs = tasks_file.resolve()
    lemming_home = get_lemming_home()

    # If the tasks file is already inside lemming home, its parent IS the project dir.
    if tasks_file_abs.parent.parent == lemming_home:
        return tasks_file_abs.parent

    # Otherwise, hash the absolute path of the tasks file to get a unique project dir.
    path_hash = hashlib.sha256(str(tasks_file_abs).encode()).hexdigest()[:12]
    return lemming_home / path_hash


def get_tasks_file_for_dir(directory: pathlib.Path) -> pathlib.Path:
    """Returns the tasks file location for a given project directory.

    Checks for a local `tasks.yml` first, then falls back to an isolated
    project tasks file in the Lemming home directory.

    Args:
        directory: The resolved absolute path to the project directory.

    Returns:
        A pathlib.Path to the tasks file for that directory.
    """
    local_tasks = directory / "tasks.yml"
    if local_tasks.exists():
        return local_tasks

    path_hash = hashlib.sha256(str(directory).encode()).hexdigest()[:12]
    return get_lemming_home() / path_hash / "tasks.yml"


def get_default_tasks_file() -> pathlib.Path:
    """Returns the default tasks file location based on the current directory.

    Returns:
        A pathlib.Path to the default tasks file.
    """
    return get_tasks_file_for_dir(pathlib.Path.cwd().resolve())


def get_working_dir(tasks_file: pathlib.Path) -> pathlib.Path:
    """Returns the intended working directory for a tasks file.

    If the tasks file is NOT in the lemming home directory, its parent
    is assumed to be the working directory.

    If it IS in the lemming home directory, we return the current
    working directory as a fallback.
    """
    tasks_file_abs = tasks_file.resolve()
    lemming_home = get_lemming_home()

    if lemming_home in tasks_file_abs.parents:
        # It's an isolated tasks file. We don't know the original source dir
        # unless it was passed to us or stored in the file.
        # For now, return CWD.
        return pathlib.Path.cwd().resolve()

    # It's a local tasks.yml file. Its parent is the project root.
    return tasks_file_abs.parent


def get_log_file(tasks_file: pathlib.Path, task_id: str) -> pathlib.Path:
    """Returns the log file path for a specific task.

    Args:
        tasks_file: Path to the tasks YAML file associated with the task.
        task_id: The unique task ID.

    Returns:
        A pathlib.Path to the log file for the given task.
    """
    project_dir = get_project_dir(tasks_file)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / f"{task_id}-runner.log"


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
