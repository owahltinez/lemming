import asyncio
import importlib.resources
import json
import logging
import os
import pathlib
import subprocess
import time
from typing import List, Optional, Dict

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from .core import (
    get_default_tasks_file,
    generate_task_id,
    load_tasks,
    save_tasks,
    lock_tasks,
    update_run_time,
    cancel_task,
    STALE_THRESHOLD,
    is_pid_alive,
)

# Paths that should not appear in the uvicorn access log (e.g. polling endpoints).
QUIET_PATHS = {"/api/data", "/api/events"}


class QuietPollFilter(logging.Filter):
    """Suppress uvicorn access-log lines for high-frequency polling endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg for path in QUIET_PATHS)


app = FastAPI()
app.state.tasks_file = get_default_tasks_file()

@app.middleware("http")
async def share_token_middleware(request: Request, call_next):
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
        
    return Response("Unauthorized", status_code=401)


class Task(BaseModel):
    id: Optional[str] = None
    description: str
    status: str = "pending"
    attempts: int = 0
    outcomes: List[str] = []
    agent: Optional[str] = None
    pid: Optional[int] = None
    completed_at: Optional[float] = None
    run_time: Optional[float] = None
    started_at: Optional[float] = None
    last_heartbeat: Optional[float] = None


class ProjectData(BaseModel):
    context: str
    tasks: List[Task]
    cwd: str
    loop_running: bool


class RunRequest(BaseModel):
    agent: Optional[str] = "gemini"
    env: Optional[Dict[str, str]] = None


@app.get("/api/data", response_model=ProjectData)
def get_data():
    return _build_project_data()


def _build_project_data() -> dict:
    """Build the project data dict (shared by GET and SSE endpoints)."""
    data = load_tasks(app.state.tasks_file)
    tasks = []
    loop_running = False
    now = time.time()

    for t in data.get("tasks", []):
        task = Task(**t)
        tasks.append(task)

        if task.status == "in_progress":
            is_stale = (
                task.last_heartbeat and now - task.last_heartbeat > STALE_THRESHOLD
            ) or (task.pid and not is_pid_alive(task.pid))
            if not is_stale:
                loop_running = True

    return {
        "context": data.get("context", ""),
        "tasks": [t.model_dump() for t in tasks],
        "cwd": os.getcwd(),
        "loop_running": loop_running,
    }


async def _sse_generator():
    """Yield SSE events when the tasks file changes."""
    last_mtime = 0.0

    # Send initial data immediately.
    project_data = _build_project_data()
    yield f"data: {json.dumps(project_data)}\n\n"
    try:
        last_mtime = os.path.getmtime(app.state.tasks_file)
    except OSError:
        pass

    while True:
        await asyncio.sleep(1)

        try:
            current_mtime = os.path.getmtime(app.state.tasks_file)
        except OSError:
            continue

        if current_mtime != last_mtime:
            last_mtime = current_mtime
            project_data = _build_project_data()
            yield f"data: {json.dumps(project_data)}\n\n"


@app.get("/api/events")
async def sse_events():
    """Server-Sent Events endpoint for real-time task updates."""
    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/agents")
def get_agents():
    return ["gemini", "aider", "claude", "codex"]


@app.post("/api/tasks")
def add_task(task: Task):
    with lock_tasks(app.state.tasks_file):
        data = load_tasks(app.state.tasks_file)
        new_task = task.model_dump(exclude_none=True)
        new_task.update(
            {
                "id": generate_task_id(),
                "status": "pending",
                "attempts": 0,
                "outcomes": [],
            }
        )
        data["tasks"].append(new_task)
        save_tasks(app.state.tasks_file, data)
    return new_task


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, update: Dict):
    with lock_tasks(app.state.tasks_file):
        data = load_tasks(app.state.tasks_file)
        target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)
        if not target:
            raise HTTPException(404, "Task not found")

        if target.get("status") == "completed" and update.get("description"):
            raise HTTPException(400, "Cannot edit description of a completed task")

        if "status" in update and update["status"] != target.get("status"):
            if target.get("status") == "in_progress":
                update_run_time(target)
            if update["status"] == "completed":
                target["completed_at"] = time.time()
            elif update["status"] == "pending":
                target.pop("completed_at", None)
                target["attempts"] = 0
            elif "completed_at" in target:
                del target["completed_at"]
            target["status"] = update["status"]

        if "description" in update:
            target["description"] = update["description"]

        save_tasks(app.state.tasks_file, data)
    return target


@app.delete("/api/tasks/completed")
def delete_completed_tasks():
    with lock_tasks(app.state.tasks_file):
        data = load_tasks(app.state.tasks_file)
        data["tasks"] = [t for t in data["tasks"] if t.get("status") != "completed"]
        save_tasks(app.state.tasks_file, data)
    return {"status": "ok"}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    with lock_tasks(app.state.tasks_file):
        data = load_tasks(app.state.tasks_file)
        data["tasks"] = [t for t in data["tasks"] if not t["id"].startswith(task_id)]
        save_tasks(app.state.tasks_file, data)
    return {"status": "ok"}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task_endpoint(task_id: str):
    if cancel_task(app.state.tasks_file, task_id):
        return {"status": "ok"}
    raise HTTPException(404, "Task not found")


@app.post("/api/context")
def update_context(update: Dict):
    with lock_tasks(app.state.tasks_file):
        data = load_tasks(app.state.tasks_file)
        data["context"] = update.get("context", "")
        save_tasks(app.state.tasks_file, data)
    return {"status": "ok"}


@app.post("/api/run")
def run_loop(request: RunRequest):
    cmd = ["lemming", "run"]
    if request.agent:
        cmd.extend(["--agent", request.agent])

    env = os.environ.copy()
    if request.env:
        env.update(request.env)

    subprocess.Popen(cmd, start_new_session=True, env=env)
    return {"status": "started"}


def _in_git_repo() -> bool:
    """Check if cwd is inside a git repository (cached after first call)."""
    if not hasattr(_in_git_repo, "_result"):
        try:
            _in_git_repo._result = (
                subprocess.run(
                    ["git", "rev-parse", "--git-dir"],
                    capture_output=True,
                ).returncode
                == 0
            )
        except Exception:
            _in_git_repo._result = False
    return _in_git_repo._result


def is_ignored(path: pathlib.Path) -> bool:
    if not _in_git_repo():
        return False
    try:
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", str(path)],
                capture_output=True,
            ).returncode
            == 0
        )
    except Exception:
        return False


@app.get("/api/files/{path:path}")
def get_files_api(path: str):
    base_path = pathlib.Path.cwd().resolve()
    target_path = (base_path / path).resolve()

    if not str(target_path).startswith(str(base_path)) or is_ignored(target_path):
        raise HTTPException(403, "Forbidden")

    if not target_path.is_dir():
        raise HTTPException(400, "Not a directory")

    contents = []
    for item in target_path.iterdir():
        if is_ignored(item):
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


@app.get("/files/{path:path}")
def serve_files(path: str):
    base_path = pathlib.Path.cwd().resolve()
    target_path = (base_path / path).resolve()

    if not str(target_path).startswith(str(base_path)) or is_ignored(target_path):
        raise HTTPException(403, "Forbidden")

    if target_path.is_dir():
        return FileResponse(web_dir / "files.html")
    if target_path.is_file():
        return FileResponse(target_path)
    raise HTTPException(404, "Not found")


@app.get("/files")
def redirect_files():
    return RedirectResponse("/files/")


web_dir = pathlib.Path(str(importlib.resources.files("lemming").joinpath("web")))
app.mount("/static", StaticFiles(directory=web_dir), name="static")


@app.get("/")
def read_index():
    return FileResponse(web_dir / "index.html")
