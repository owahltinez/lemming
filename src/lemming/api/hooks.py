import fastapi

from .. import prompts
from . import context

router = fastapi.APIRouter()


@router.get("/api/hooks")
def list_hooks(request: fastapi.Request, project: str | None = None):
    """List all available orchestrator hooks (built-in and project-specific)."""
    tasks_file = context.resolve_tasks_file(request.app.state, project)
    return prompts.list_hooks(tasks_file)
