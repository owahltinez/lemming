"""YAML persistence and file locking for the roadmap state."""

import contextlib
import fcntl
import functools
import logging
import os
import pathlib
import subprocess

import yaml

from . import models, paths

logger = logging.getLogger(__name__)

STALE_THRESHOLD = 30  # seconds

# Bound on the ps probe used for zombie detection off Linux.
PS_PROBE_TIMEOUT = 5
LOOP_LOCK_FILENAME = ".lemming_loop.lock"

# Unparseable tasks files are copied to "<tasks file>.corrupt", then to
# ".corrupt.1", ".corrupt.2", ... so a later corruption never overwrites the
# evidence from an earlier one.
CORRUPT_SUFFIX = ".corrupt"
MAX_CORRUPT_BACKUPS = 100


class LoopAlreadyRunningError(RuntimeError):
    """Raised when another process owns the orchestrator loop lock."""


class CorruptedTasksError(RuntimeError):
    """Raised when a tasks file exists but cannot be parsed.

    Callers must never downgrade this to "no tasks yet": inferring an empty
    roadmap makes the next save overwrite the only copy of the user's tasks.
    """

    def __init__(
        self,
        tasks_file: pathlib.Path,
        error: Exception,
        backup_file: pathlib.Path | None = None,
    ):
        """Builds an actionable message naming the file and its backup.

        Args:
            tasks_file: Path to the tasks file that could not be parsed.
            error: The underlying parse error.
            backup_file: Path holding a copy of the unparseable bytes, if one
                could be written.
        """
        self.tasks_file = tasks_file
        self.error = error
        self.backup_file = backup_file
        recovery = (
            f"a copy of the unreadable bytes is at {backup_file}"
            if backup_file
            else "the unreadable file was left untouched"
        )
        super().__init__(
            f"Tasks file {tasks_file} could not be parsed: {error}. "
            "Refusing to continue so it is not overwritten with an empty "
            f"roadmap ({recovery}). Repair the YAML by hand or restore a "
            "backup, then retry."
        )


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


@contextlib.contextmanager
def read_lock_tasks(tasks_file: pathlib.Path):
    """Context manager that acquires a shared (read) lock on the tasks file.

    Use this around load_tasks() in read-only paths (e.g. API polling) to
    prevent reading a partially-written file. Do NOT nest inside lock_tasks()
    as that will deadlock on Linux.

    Args:
        tasks_file: Path to the tasks YAML file.
    """
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    if not tasks_file.exists():
        tasks_file.write_text("{}", encoding="utf-8")

    lock_path = tasks_file.with_suffix(".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _corrupt_backup_paths(tasks_file: pathlib.Path):
    """Yields the backup paths to try, in the order they should be used."""
    base = tasks_file.with_name(tasks_file.name + CORRUPT_SUFFIX)
    yield base
    for index in range(1, MAX_CORRUPT_BACKUPS):
        yield base.with_name(f"{base.name}.{index}")


def _backup_corrupted_tasks(
    tasks_file: pathlib.Path, raw: bytes
) -> pathlib.Path | None:
    """Preserves the bytes of an unparseable tasks file beside the original.

    Backups are only ever created, never overwritten, so a second corruption
    cannot destroy the evidence from the first. Bytes that are already
    preserved reuse their backup, so repeatedly loading the same broken file
    does not pile up copies.

    Args:
        tasks_file: Path to the tasks file that failed to parse.
        raw: The exact bytes read from that file.

    Returns:
        The backup holding these bytes, or None if none could be written.
    """
    for candidate in _corrupt_backup_paths(tasks_file):
        try:
            # Exclusive creation keeps a concurrent loader from clobbering a
            # backup between the existence check and the write.
            with open(candidate, "xb") as backup_file:
                backup_file.write(raw)
        except FileExistsError:
            # Anything already occupying the path (including a stale backup of
            # different bytes, or a directory) is left alone.
            with contextlib.suppress(OSError):
                if candidate.read_bytes() == raw:
                    logger.info(
                        "Corrupted tasks file %s already backed up at %s",
                        tasks_file,
                        candidate,
                    )
                    return candidate
            continue
        except OSError as e:
            logger.error(
                "Failed to back up corrupted tasks file %s to %s: %s",
                tasks_file,
                candidate,
                e,
            )
            return None

        logger.info(
            "Backed up corrupted tasks file %s to %s", tasks_file, candidate
        )
        return candidate

    logger.error(
        "Ran out of backup slots for corrupted tasks file %s (limit %d)",
        tasks_file,
        MAX_CORRUPT_BACKUPS,
    )
    return None


def load_tasks(tasks_file: pathlib.Path) -> models.Roadmap:
    """Loads tasks from a YAML file.

    Args:
        tasks_file: Path to the tasks YAML file.

    Returns:
        A Roadmap containing the goal and list of tasks.

    Raises:
        CorruptedTasksError: If the file exists but does not hold a roadmap,
            whether because it does not parse, parses to something that is not
            a roadmap, or has been emptied. The bytes are backed up and the
            file is left untouched.
    """
    if not tasks_file.exists():
        return models.Roadmap(
            goal="# Long-Term Goal\n\nDescribe what 'done' looks like for "
            "this project.",
            tasks=[],
        )

    # Read the bytes up front so the exact on-disk content can be preserved
    # even when it is not valid UTF-8 (e.g. a truncated or clobbered file).
    raw = tasks_file.read_bytes()
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
        # A file that parses to nothing (empty, whitespace, "null") has lost
        # its contents: the bootstrap in lock_tasks() writes "{}", which parses
        # to a mapping, so None means the roadmap was truncated away rather
        # than never written.
        if data is None:
            raise ValueError("file contains no roadmap")
        # Pydantic's ValidationError is a ValueError, so YAML of the wrong
        # shape (a bare list, prose, an HTML error page) lands here too.
        return models.Roadmap.model_validate(data)
    except (yaml.YAMLError, UnicodeDecodeError, ValueError) as e:
        # Corruption has to stay loud: returning an empty roadmap here would
        # let the next save_tasks() destroy the only copy of the roadmap. The
        # raised error carries the file, the backup, and the parse failure, so
        # it is logged where it is handled instead of twice.
        raise CorruptedTasksError(
            tasks_file, e, _backup_corrupted_tasks(tasks_file, raw)
        ) from e


class _BlockStyleDumper(yaml.SafeDumper):
    """YAML dumper that forces multiline strings to use block style (|)."""

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

    # Use a temporary file for atomic write
    temp_file = tasks_file.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
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

    # Atomically replace the old file with the new one
    os.replace(temp_file, tasks_file)


def _get_loop_lock_path(tasks_file: pathlib.Path) -> pathlib.Path:
    project_dir = paths.get_project_dir(tasks_file)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / LOOP_LOCK_FILENAME


def acquire_loop_lock(tasks_file: pathlib.Path) -> None:
    """Acquire the loop lock unless another live process owns it."""
    with lock_tasks(tasks_file):
        existing_pid = get_loop_pid(tasks_file)
        if existing_pid is not None and is_pid_alive(existing_pid):
            raise LoopAlreadyRunningError(
                f"Another loop is already running (pid {existing_pid})."
            )
        _get_loop_lock_path(tasks_file).write_text(str(os.getpid()))


def release_loop_lock(tasks_file: pathlib.Path) -> None:
    """Release the loop lock if the current process owns it."""
    with lock_tasks(tasks_file):
        if get_loop_pid(tasks_file) == os.getpid():
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


@functools.cache
def _has_proc_filesystem() -> bool:
    """Returns whether /proc exposes process state, as it does on Linux."""
    return pathlib.Path("/proc").is_dir()


def _is_zombie(pid: int) -> bool:
    """Returns whether a PID has exited but not yet been reaped.

    A zombie still answers ``kill(pid, 0)``, so callers that only signal-probe
    would treat a finished process as running until its parent reaps it.
    """
    if _has_proc_filesystem():
        try:
            status_path = pathlib.Path(f"/proc/{pid}/status")
            if status_path.exists():
                for line in status_path.read_text().splitlines():
                    if line.startswith("State:"):
                        return line.split()[1] == "Z"
        except OSError:
            pass
        return False

    # Without /proc (macOS and the BSDs), ps reports the same state code.
    try:
        result = subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=PS_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return result.stdout.strip().startswith("Z")


def is_pid_alive(pid: int) -> bool:
    """Return whether a process is alive and is not a zombie."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    return not _is_zombie(pid)


def is_loop_running(tasks_file: pathlib.Path) -> bool:
    """Return whether a live process owns the lock, clearing stale locks."""
    with lock_tasks(tasks_file):
        pid = get_loop_pid(tasks_file)
        if pid is not None and is_pid_alive(pid):
            return True
        _get_loop_lock_path(tasks_file).unlink(missing_ok=True)
        return False
