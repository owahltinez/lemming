import hashlib
import logging
import os
import pathlib
import stat
import subprocess

logger = logging.getLogger(__name__)


def _parse_dotenv(path: pathlib.Path) -> dict[str, str]:
    """Parse a .env file into a dict, skipping comments and blank lines."""
    result: dict[str, str] = {}
    try:
        for lineno, raw_line in enumerate(path.read_text().splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            # Optional "export " prefix
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                logger.warning("%s:%d: skipping malformed line (no '=')", path, lineno)
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            # Strip surrounding quotes from value
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            result[key] = value
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
    return result


def _check_permissions(path: pathlib.Path) -> None:
    """Warn if the .env file is readable by group or others."""
    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            logger.warning(
                "%s has overly permissive permissions (%o). "
                "Consider restricting to 600.",
                path,
                stat.S_IMODE(mode),
            )
    except OSError:
        pass


def load_dotenv(project_dir: pathlib.Path | None = None) -> None:
    """Load environment variables from .env files.

    Precedence (later wins, but real env vars always take priority):
      1. ~/.local/lemming/.env  (global defaults)
      2. <project_dir>/.env     (project overrides)

    Existing environment variables are never overwritten.
    """
    env_files: list[pathlib.Path] = []

    global_env = get_lemming_home() / ".env"
    if global_env.is_file():
        env_files.append(global_env)

    if project_dir is not None:
        project_env = project_dir / ".env"
        if project_env.is_file() and project_env != global_env:
            env_files.append(project_env)

    merged: dict[str, str] = {}
    for env_file in env_files:
        _check_permissions(env_file)
        merged.update(_parse_dotenv(env_file))

    # Only set vars that aren't already in the environment
    for key, value in merged.items():
        if key not in os.environ:
            os.environ[key] = value


def get_lemming_home() -> pathlib.Path:
    """Determines the Lemming home directory from the environment or default.

    Returns:
        A pathlib.Path representing the Lemming home directory.
    """
    home_override = os.environ.get("LEMMING_HOME")
    if home_override:
        return pathlib.Path(home_override)
    return pathlib.Path.home() / ".local" / "lemming"


def get_global_hooks_dir() -> pathlib.Path:
    """Returns the global hooks directory in Lemming home."""
    return get_lemming_home() / "hooks"


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
