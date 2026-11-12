"""Helpers to start the orchestrator loop in the background."""

import os
import pathlib
import subprocess
import sys
import typing

import fastapi

from ..tasks import lifecycle


def spawn_loop(
    tasks_file: pathlib.Path,
    cwd: pathlib.Path | None = None,
    verbose: bool = False,
    env: dict[str, str] | None = None,
    run_args: typing.Sequence[str] = (),
    log: typing.IO | None = None,
) -> int:
    """Starts `lemming run` in a new session that outlives its caller.

    Args:
        tasks_file: Tasks file the loop works through.
        cwd: Project directory to run the loop from.
        verbose: Whether the loop prints verbose output.
        env: Environment for the loop; defaults to the current one.
        run_args: Extra arguments for the `run` command.
        log: File for the loop's output; inherits the caller's when None.

    Returns:
        The PID of the loop process.
    """
    # Use sys.executable -m lemming.main to ensure we use the same environment
    # and pass the explicit tasks file.
    cmd = [sys.executable, "-m", "lemming.main"]
    if verbose:
        cmd.append("--verbose")
    if cwd is not None:
        cmd.extend(["-C", str(cwd)])
    cmd.extend(["--tasks-file", str(tasks_file), "run", *run_args])

    process = subprocess.Popen(
        cmd,
        start_new_session=True,
        env=env if env is not None else os.environ.copy(),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
    )
    return process.pid


def start_loop_if_needed(
    app_state: fastapi.datastructures.State,
    tasks_file: pathlib.Path,
    cwd: pathlib.Path | None = None,
):
    """Start the orchestrator loop unless disabled or already running."""
    if getattr(app_state, "disable_auto_start", False):
        return

    if lifecycle.is_loop_running(tasks_file):
        return

    spawn_loop(tasks_file, cwd, verbose=getattr(app_state, "verbose", False))
