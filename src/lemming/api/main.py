import importlib.resources
import pathlib

import fastapi
import fastapi.responses
import fastapi.staticfiles

from .. import paths
from . import auth
from . import config
from . import directories
from . import files
from . import hooks
from . import logging as lemming_logging
from . import tasks

# Re-export for backward compatibility and tests
QuietPollFilter = lemming_logging.QuietPollFilter


class FilteredStaticFiles(fastapi.staticfiles.StaticFiles):
    """
    Subclass of StaticFiles that filters out web test files.
    """

    def lookup_path(self, path: str):
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

# Static files and root routes
web_dir = pathlib.Path(str(importlib.resources.files("lemming").joinpath("web")))
app.mount("/static", FilteredStaticFiles(directory=web_dir), name="static")


@app.get("/")
def read_index():
    return fastapi.responses.FileResponse(web_dir / "index.html")
