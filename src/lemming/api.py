import importlib.resources
import logging
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


@app.get("/api/data", response_model=tasks.ProjectData)
def get_data():
    return tasks.get_project_data(app.state.tasks_file)


@app.get("/api/runners")
def get_runners():
    return ["gemini", "aider", "claude", "codex"]


class AddTaskRequest(pydantic.BaseModel):
    description: str
    runner: str | None = None
    index: int = -1
    parent: str | None = None


@app.post("/api/tasks")
def add_task(task: AddTaskRequest):
    return tasks.add_task(
        app.state.tasks_file,
        task.description,
        task.runner,
        index=task.index,
        parent=task.parent,
    )


@app.get("/api/tasks/{task_id}", response_model=tasks.Task)
def get_task(task_id: str):

    data = tasks.load_tasks(app.state.tasks_file)
    target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
    if not target:
        raise fastapi.HTTPException(404, "Task not found")
    return target


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, update: dict):
    status = update.get("status")

    # Validation: require outcomes if completing or failing from the UI,
    # but not if we are just marking a completed task as pending (uncomplete).
    require_outcomes = False
    if status in ("completed", "pending"):
        data = tasks.load_tasks(app.state.tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if target and target.status != "completed":
            require_outcomes = True

    try:
        return tasks.update_task(
            app.state.tasks_file,
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
def delete_completed_tasks():
    tasks.delete_tasks(app.state.tasks_file, completed_only=True)
    return {"status": "ok"}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    tasks.delete_tasks(app.state.tasks_file, task_id=task_id)
    return {"status": "ok"}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task_endpoint(task_id: str):
    if tasks.cancel_task(app.state.tasks_file, task_id):
        return {"status": "ok"}
    raise fastapi.HTTPException(404, "Task not found")


@app.post("/api/tasks/{task_id}/clear")
def clear_task_endpoint(task_id: str):
    try:
        tasks.reset_task(app.state.tasks_file, task_id)
        return {"status": "ok"}
    except ValueError as e:
        raise fastapi.HTTPException(404, str(e))


@app.get("/api/tasks/{task_id}/log")
def get_task_log(task_id: str, name: str = "runner"):
    if name not in ("runner", "review"):
        raise fastapi.HTTPException(400, "Invalid log name")
    log_file = paths.get_log_file(app.state.tasks_file, task_id, name)
    if not log_file.exists():
        return {"log": ""}
    return {"log": log_file.read_text(encoding="utf-8")}


@app.post("/api/context")
def update_context(update: dict):
    tasks.update_context(app.state.tasks_file, update.get("context", ""))
    return {"status": "ok"}


@app.post("/api/run")
def run_loop(request: RunRequest):
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
            str(app.state.tasks_file),
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
    base_path = pathlib.Path.cwd().resolve()
    target_path = (base_path / path).resolve()

    if not str(target_path).startswith(str(base_path)) or paths.is_ignored(target_path):
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
    base_path = pathlib.Path.cwd().resolve()
    target_path = (base_path / path).resolve()

    if not str(target_path).startswith(str(base_path)) or paths.is_ignored(target_path):
        raise fastapi.HTTPException(403, "Forbidden")

    if target_path.is_dir():
        return fastapi.responses.FileResponse(web_dir / "files.html")
    if target_path.is_file():
        return fastapi.responses.FileResponse(target_path)
    raise fastapi.HTTPException(404, "Not found")


@app.get("/files")
def redirect_files():
    return fastapi.responses.RedirectResponse("/files/")


web_dir = pathlib.Path(str(importlib.resources.files("lemming").joinpath("web")))
app.mount("/static", fastapi.staticfiles.StaticFiles(directory=web_dir), name="static")


@app.get("/")
def read_index():
    return fastapi.responses.FileResponse(web_dir / "index.html")
