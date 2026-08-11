"""FastAPI application setup: middleware, routers, and static files."""

import importlib.resources
import logging
import pathlib

import fastapi
import fastapi.responses
import fastapi.staticfiles

from .. import paths, persistence
from . import auth, config, directories, files, hooks, tasks
from . import logging as lemming_logging

logger = logging.getLogger(__name__)

# Re-exported so logging configs can reference lemming.api.QuietPollFilter
QuietPollFilter = lemming_logging.QuietPollFilter


class FilteredStaticFiles(fastapi.staticfiles.StaticFiles):
    """Subclass of StaticFiles that filters out web test files."""

    def lookup_path(self, path: str):
        """Hide .spec.js and .test.js files from static file lookups."""
        if path.endswith(".spec.js") or path.endswith(".test.js"):
            return "", None
        return super().lookup_path(path)


app = fastapi.FastAPI()
app.state.tasks_file = paths.get_default_tasks_file()
app.state.root = pathlib.Path.cwd().resolve()
app.state.disable_auto_start = False

# Middleware
app.middleware("http")(auth.share_token_middleware)

# Include Routers
app.include_router(tasks.router)
app.include_router(files.router)
app.include_router(directories.router)
app.include_router(hooks.router)
app.include_router(config.router)


@app.exception_handler(persistence.CorruptedTasksError)
def handle_corrupted_tasks(
    request: fastapi.Request, exc: Exception
) -> fastapi.responses.JSONResponse:
    """Reports an unreadable tasks file in the shape the UI already expects.

    Without this every route touching the roadmap would answer a corrupt file
    with a raw stack trace instead of an actionable message.
    """
    logger.error("%s could not read the tasks file: %s", request.url.path, exc)
    return fastapi.responses.JSONResponse(
        status_code=500, content={"detail": str(exc)}
    )


# Static files and root routes
web_dir = pathlib.Path(
    str(importlib.resources.files("lemming").joinpath("web"))
)
app.mount("/static", FilteredStaticFiles(directory=web_dir), name="static")


@app.get("/")
def read_index():
    """Serve the main web UI page."""
    return fastapi.responses.FileResponse(web_dir / "index.html")
