import importlib.resources
import logging
import mimetypes
import os
import pathlib
import subprocess
import sys

import fastapi
import fastapi.responses
import fastapi.staticfiles
import pydantic

from . import paths
from . import tasks

# Paths that should not appear in the uvicorn access log (e.g. polling endpoints).
QUIET_PATHS = {"/api/data", "GET /api/tasks/"}


class QuietPollFilter(logging.Filter):
    """Suppress uvicorn access-log lines for high-frequency polling endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg for path in QUIET_PATHS)


app = fastapi.FastAPI()
app.state.tasks_file = paths.get_default_tasks_file()
app.state.root = pathlib.Path.cwd().resolve()


def resolve_tasks_file(project: str | None = None) -> pathlib.Path:
    """Resolve a project query parameter to a tasks file path.

    When *project* is ``None`` or empty the server-wide default is returned.
    Otherwise the value is treated as a relative directory under the server
    root and the tasks file path is derived deterministically.
    """
    if not project:
        return app.state.tasks_file

    root = app.state.root
    target = (root / project).resolve()
    if not target.is_relative_to(root):
        raise fastapi.HTTPException(403, "Path is outside the server root")
    if not target.is_dir():
        raise fastapi.HTTPException(400, "Not a directory")

    return paths.get_tasks_file_for_dir(target)


@app.middleware("http")
async def share_token_middleware(request: fastapi.Request, call_next):
    share_token = getattr(request.app.state, "share_token", None)
    if not share_token:
        return await call_next(request)

    host = request.headers.get("host", "")
    if host.startswith("127.0.0.1") or host.startswith("localhost"):
        return await call_next(request)

    token = request.query_params.get("token")
    if token == share_token:
        response = await call_next(request)
        response.set_cookie(key="lemming_share_token", value=token, httponly=True)
        return response

    cookie_token = request.cookies.get("lemming_share_token")
    if cookie_token == share_token:
        return await call_next(request)

    return fastapi.Response("Unauthorized", status_code=401)


class RunRequest(pydantic.BaseModel):
    runner: str | None = "gemini"
    env: dict[str, str] | None = None
    retries: int | None = None
    review: bool = False


@app.get("/api/directories")
def list_directories(path: str = ""):
    """List subdirectories under the server root for the project picker."""
    root = app.state.root
    target = (root / path).resolve() if path else root
    if not target.is_relative_to(root):
        raise fastapi.HTTPException(403, "Path is outside the server root")
    if not target.is_dir():
        raise fastapi.HTTPException(400, "Not a directory")

    dirs = []
    for item in sorted(target.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            rel = item.relative_to(root)
            dirs.append({"name": item.name, "path": str(rel)})
    return {"path": path, "directories": dirs}


@app.get("/api/data", response_model=tasks.ProjectData)
def get_data(project: str | None = None):
    return tasks.get_project_data(resolve_tasks_file(project))


@app.get("/api/runners")
def get_runners():
    return ["gemini", "aider", "claude", "codex"]


class AddTaskRequest(pydantic.BaseModel):
    description: str
    runner: str | None = None
    index: int = -1
    parent: str | None = None


@app.post("/api/tasks")
def add_task(task: AddTaskRequest, project: str | None = None):
    return tasks.add_task(
        resolve_tasks_file(project),
        task.description,
        task.runner,
        index=task.index,
        parent=task.parent,
    )


@app.get("/api/tasks/{task_id}", response_model=tasks.Task)
def get_task(task_id: str, project: str | None = None):
    data = tasks.load_tasks(resolve_tasks_file(project))
    target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
    if not target:
        raise fastapi.HTTPException(404, "Task not found")
    return target


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, update: dict, project: str | None = None):
    tasks_file = resolve_tasks_file(project)
    status = update.get("status")

    # Validation: require outcomes if completing or failing from the UI,
    # but not if we are just marking a completed task as pending (uncomplete).
    require_outcomes = False
    if status in ("completed", "pending"):
        data = tasks.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if target and target.status != "completed":
            require_outcomes = True

    try:
        return tasks.update_task(
            tasks_file,
            task_id,
            description=update.get("description"),
            runner=update.get("runner"),
            index=update.get("index"),
            status=status,
            require_outcomes=require_outcomes,
            parent=update.get("parent"),
        )
    except ValueError as e:
        if "not found" in str(e):
            raise fastapi.HTTPException(404, str(e))
        raise fastapi.HTTPException(400, str(e))


@app.delete("/api/tasks/completed")
def delete_completed_tasks(project: str | None = None):
    tasks.delete_tasks(resolve_tasks_file(project), completed_only=True)
    return {"status": "ok"}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, project: str | None = None):
    tasks.delete_tasks(resolve_tasks_file(project), task_id=task_id)
    return {"status": "ok"}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task_endpoint(task_id: str, project: str | None = None):
    if tasks.cancel_task(resolve_tasks_file(project), task_id):
        return {"status": "ok"}
    raise fastapi.HTTPException(404, "Task not found")


@app.post("/api/tasks/{task_id}/clear")
def clear_task_endpoint(task_id: str, project: str | None = None):
    try:
        tasks.reset_task(resolve_tasks_file(project), task_id)
        return {"status": "ok"}
    except ValueError as e:
        raise fastapi.HTTPException(404, str(e))


@app.get("/api/tasks/{task_id}/log")
def get_task_log(task_id: str, name: str = "runner", project: str | None = None):
    if name not in ("runner", "review"):
        raise fastapi.HTTPException(400, "Invalid log name")
    log_file = paths.get_log_file(resolve_tasks_file(project), task_id, name)
    if not log_file.exists():
        return {"log": ""}
    return {"log": log_file.read_text(encoding="utf-8")}


@app.post("/api/context")
def update_context(update: dict, project: str | None = None):
    tasks.update_context(resolve_tasks_file(project), update.get("context", ""))
    return {"status": "ok"}


@app.post("/api/run")
def run_loop(request: RunRequest, project: str | None = None):
    tasks_file = resolve_tasks_file(project)
    # Use sys.executable -m lemming.main to ensure we use the same environment
    # and pass the explicit tasks file.
    cmd = [
        sys.executable,
        "-m",
        "lemming.main",
    ]
    if getattr(app.state, "verbose", False):
        cmd.append("--verbose")
    cmd.extend(
        [
            "--tasks-file",
            str(tasks_file),
            "run",
        ]
    )

    if request.retries is not None:
        cmd.extend(["--retries", str(request.retries)])

    if request.runner:
        cmd.extend(["--runner", request.runner])

    if request.review:
        cmd.append("--auto-review")

    env = os.environ.copy()
    if request.env:
        env.update(request.env)

    subprocess.Popen(cmd, start_new_session=True, env=env)
    return {"status": "started"}


@app.get("/api/files/{path:path}")
def get_files_api(path: str):
    base_path = app.state.root
    target_path = (base_path / path).resolve()

    if not target_path.is_relative_to(base_path) or paths.is_ignored(target_path):
        raise fastapi.HTTPException(403, "Forbidden")

    if not target_path.is_dir():
        raise fastapi.HTTPException(400, "Not a directory")

    contents = []
    for item in target_path.iterdir():
        if paths.is_ignored(item):
            continue
        rel_path = item.relative_to(base_path)
        is_dir = item.is_dir()
        stats = item.stat()
        contents.append(
            {
                "name": item.name + ("/" if is_dir else ""),
                "path": str(rel_path),
                "is_dir": is_dir,
                "size": None if is_dir else stats.st_size,
                "modified": stats.st_mtime,
            }
        )
    return {
        "path": path,
        "contents": sorted(
            contents, key=lambda x: (not x["is_dir"], x["name"].lower())
        ),
    }


@app.get("/tasks/{task_id}/log")
def serve_task_log(task_id: str):
    return fastapi.responses.FileResponse(web_dir / "logs.html")


@app.get("/files/{path:path}")
def serve_files(path: str):
    base_path = app.state.root
    target_path = (base_path / path).resolve()

    if not target_path.is_relative_to(base_path) or paths.is_ignored(target_path):
        raise fastapi.HTTPException(403, "Forbidden")

    if target_path.is_dir():
        return fastapi.responses.FileResponse(web_dir / "files.html")
    if target_path.is_file():
        # Guess the MIME type to identify binary formats.
        guess, _ = mimetypes.guess_type(target_path)

        # Consider images, video, audio, PDFs, and common archives as "binary" to be served as-is.
        # This allows images to render and archives/PDFs to be downloaded or shown correctly.
        is_binary = guess and (
            guess.startswith(("image/", "video/", "audio/"))
            or guess
            in (
                "application/pdf",
                "application/wasm",
                "application/zip",
                "application/x-zip-compressed",
            )
        )

        # Special case: .ts files are frequently misidentified as video/mp2t.
        # In a development project context, these are almost always TypeScript source files.
        if is_binary and target_path.suffix.lower() == ".ts":
            is_binary = False

        if is_binary:
            return fastapi.responses.FileResponse(target_path)

        # For everything else (including HTML, JS, CSS, and unknown formats),
        # force text/plain to ensure browser views source code instead of rendering/executing.
        return fastapi.responses.FileResponse(target_path, media_type="text/plain")

    raise fastapi.HTTPException(404, "Not found")


@app.get("/files")
def redirect_files():
    return fastapi.responses.RedirectResponse("/files/")


web_dir = pathlib.Path(str(importlib.resources.files("lemming").joinpath("web")))
app.mount("/static", fastapi.staticfiles.StaticFiles(directory=web_dir), name="static")


@app.get("/")
def read_index():
    return fastapi.responses.FileResponse(web_dir / "index.html")
