"""API routes for listing and toggling orchestrator hooks."""

import pathlib

import fastapi
import pydantic

from .. import hooks
from . import context

router = fastapi.APIRouter()


def _hook_payload(tasks_file: pathlib.Path) -> list[dict]:
    """Serializes resolved hooks for API responses."""
    return [
        {
            "name": h.name,
            "priority": h.priority,
            "source": h.source,
            "masked": h.masked,
            "runs_on_failure": h.priority
            >= hooks.FAILURE_HOOK_PRIORITY,
        }
        for h in hooks.resolve_hooks(tasks_file)
    ]


@router.get("/api/hooks")
def list_hooks(request: fastapi.Request, project: str | None = None):
    """List resolved orchestrator hooks in execution order."""
    tasks_file = context.resolve_tasks_file(request.app.state, project)
    return _hook_payload(tasks_file)


class HookToggle(pydantic.BaseModel):
    """Request body for enabling or disabling a hook."""

    name: str
    enabled: bool


@router.post("/api/hooks")
def toggle_hook(
    request: fastapi.Request,
    toggle: HookToggle,
    project: str | None = None,
):
    """Enable or disable a hook by removing or creating its project mask."""
    tasks_file = context.resolve_tasks_file(request.app.state, project)
    try:
        if toggle.enabled:
            hooks.enable_hooks([toggle.name], tasks_file)
        else:
            hooks.disable_hooks([toggle.name], tasks_file)
    except ValueError as e:
        raise fastapi.HTTPException(status_code=400, detail=str(e))
    return _hook_payload(tasks_file)
