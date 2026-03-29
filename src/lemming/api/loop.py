import os
import pathlib
import subprocess
import sys

import fastapi

from .. import tasks


def start_loop_if_needed(
    app_state: fastapi.datastructures.State,
    tasks_file: pathlib.Path,
    cwd: pathlib.Path | None = None,
):
    """Automatically start the orchestrator loop if it is not already running."""
    if getattr(app_state, "disable_auto_start", False):
        return

    if tasks.is_loop_running(tasks_file):
        return

    # Use sys.executable -m lemming.main to ensure we use the same environment
    # and pass the explicit tasks file.
    cmd = [
        sys.executable,
        "-m",
        "lemming.main",
    ]
    if getattr(app_state, "verbose", False):
        cmd.append("--verbose")
    cmd.extend(
        [
            "--tasks-file",
            str(tasks_file),
            "run",
        ]
    )

    # Start the loop in a new session so it outlives the request.
    subprocess.Popen(cmd, start_new_session=True, env=os.environ.copy(), cwd=cwd)
