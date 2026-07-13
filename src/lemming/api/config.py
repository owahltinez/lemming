"""API routes for runner discovery, project configuration, and loop runs."""

import os
import subprocess
import sys

import fastapi
import pydantic

from .. import models, tasks
from . import context

router = fastapi.APIRouter()


@router.get("/api/runners")
def get_runners():
    """List the names of all known task runners."""
    return list(models.KNOWN_RUNNERS)


@router.post("/api/context")
def update_context(
    request: fastapi.Request, update: dict, project: str | None = None
):
    """Update the shared project context passed to task runners."""
    tasks_file = context.resolve_tasks_file(request.app.state, project)
    tasks.update_context(tasks_file, update.get("context", ""))
    return {"status": "ok"}


@router.post("/api/config")
def update_config(
    request: fastapi.Request,
    config: tasks.RoadmapConfig,
    project: str | None = None,
):
    """Replace the roadmap configuration and return the saved value."""
    tasks_file = context.resolve_tasks_file(request.app.state, project)
    with tasks.lock_tasks(tasks_file):
        data = tasks.load_tasks(tasks_file)
        data.config = config
        tasks.save_tasks(tasks_file, data)
    return data.config


class RunRequest(pydantic.BaseModel):
    """Request body for starting the orchestrator loop."""

    env: dict[str, str] | None = None


@router.post("/api/run")
def run_loop(
    request: fastapi.Request,
    run_request: RunRequest,
    project: str | None = None,
):
    """Start the orchestrator loop as a detached background process."""
    tasks_file = context.resolve_tasks_file(request.app.state, project)
    project_dir = context.resolve_project_dir(request.app.state, project)

    # Use sys.executable -m lemming.main to ensure we use the same environment
    # and pass the explicit tasks file.
    cmd = [
        sys.executable,
        "-m",
        "lemming.main",
    ]
    if getattr(request.app.state, "verbose", False):
        cmd.append("--verbose")
    cmd.extend(
        [
            "--tasks-file",
            str(tasks_file),
            "run",
        ]
    )

    env = os.environ.copy()
    if run_request.env:
        env.update(run_request.env)

    subprocess.Popen(cmd, start_new_session=True, env=env, cwd=project_dir)
    return {"status": "started"}
