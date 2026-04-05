import contextlib
import fcntl
import os
import pathlib

import yaml

from . import models, paths

STALE_THRESHOLD = 30  # seconds
LOOP_LOCK_FILENAME = ".lemming_loop.lock"


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
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def load_tasks(tasks_file: pathlib.Path) -> models.Roadmap:
    """Loads tasks from a YAML file.

    Args:
        tasks_file: Path to the tasks YAML file.

    Returns:
        A Roadmap containing the context and list of tasks.
    """
    if not tasks_file.exists():
        return models.Roadmap(
            context="# Project Context\n\nAdd your guidelines here.",
            tasks=[],
        )

    lock_path = tasks_file.with_suffix(".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_SH)
        try:
            with open(tasks_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if not data:
                    data = {}
                return models.Roadmap.model_validate(data)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


class _BlockStyleDumper(yaml.SafeDumper):
    """Custom YAML dumper that forces multiline strings to use block style (|)."""

    def represent_scalar(self, tag, value, style=None):
        if tag == "tag:yaml.org,2002:str" and "\n" in value:
            style = "|"
        return super().represent_scalar(tag, value, style)


def save_tasks(tasks_file: pathlib.Path, data: models.Roadmap) -> None:
    """Saves the roadmap data to a YAML file.

    Args:
        tasks_file: Path to the tasks YAML file.
        data: The Roadmap to save.
    """
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    with open(tasks_file, "w", encoding="utf-8") as f:
        # Exclude runtime-computed fields from the YAML file.
        exclude = {
            "tasks": {
                "__all__": {
                    "index",
                    "has_runner_log",
                }
            }
        }
        yaml.dump(
            data.model_dump(exclude_none=True, mode="json", exclude=exclude),
            f,
            Dumper=_BlockStyleDumper,
            default_flow_style=False,
            sort_keys=False,
            width=1000,
        )


def _get_loop_lock_path(tasks_file: pathlib.Path) -> pathlib.Path:
    project_dir = paths.get_project_dir(tasks_file)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / LOOP_LOCK_FILENAME


def acquire_loop_lock(tasks_file: pathlib.Path) -> None:
    """Write a loop lock file with the current PID."""
    _get_loop_lock_path(tasks_file).write_text(str(os.getpid()))


def release_loop_lock(tasks_file: pathlib.Path) -> None:
    """Remove the loop lock file."""
    _get_loop_lock_path(tasks_file).unlink(missing_ok=True)


def get_loop_pid(tasks_file: pathlib.Path) -> int | None:
    """Returns the PID of the running orchestrator loop, if any."""
    lock_path = _get_loop_lock_path(tasks_file)
    if not lock_path.exists():
        return None
    try:
        return int(lock_path.read_text().strip())
    except (ValueError, OSError):
        return None
