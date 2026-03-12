import os
import pathlib
import subprocess
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from .core import (
    get_default_tasks_file,
    generate_task_id,
    load_tasks,
    save_tasks,
    lock_tasks,
    cancel_task,
    STALE_THRESHOLD,
    is_pid_alive,
)

app = FastAPI()

# Shared state (simplified for this POC)
TASKS_FILE = get_default_tasks_file()


def set_tasks_file(path: pathlib.Path):
    """Sets the tasks file to use for the API."""
    global TASKS_FILE
    TASKS_FILE = path


class Task(BaseModel):
    id: Optional[str] = None
    description: str
    status: str = "pending"
    attempts: int = 0
    outcomes: List[str] = []
    agent: Optional[str] = None
    last_heartbeat: Optional[float] = None
    pid: Optional[int] = None
    completed_at: Optional[float] = None


class ProjectData(BaseModel):
    context: str
    tasks: List[Task]
    cwd: str
    loop_running: bool


class ContextUpdate(BaseModel):
    context: str


class RunRequest(BaseModel):
    agent: Optional[str] = "gemini"


class TaskUpdate(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = None


@app.get("/api/data", response_model=ProjectData)
async def get_data():
    data = load_tasks(TASKS_FILE)
    # Ensure all tasks have all fields to match the Task model
    tasks = []
    loop_running = False
    now = time.time()

    for t in data.get("tasks", []):
        task_obj = Task(
            id=t.get("id"),
            description=t.get("description", ""),
            status=t.get("status", "pending"),
            attempts=t.get("attempts", 0),
            outcomes=t.get("outcomes", []),
            agent=t.get("agent"),
            last_heartbeat=t.get("last_heartbeat"),
            pid=t.get("pid"),
            completed_at=t.get("completed_at"),
        )
        tasks.append(task_obj)

        if task_obj.status == "in_progress":
            is_stale = False
            if (
                task_obj.last_heartbeat
                and now - task_obj.last_heartbeat > STALE_THRESHOLD
            ):
                is_stale = True
            elif task_obj.pid and not is_pid_alive(task_obj.pid):
                is_stale = True

            if not is_stale:
                loop_running = True

    return ProjectData(
        context=data.get("context", ""),
        tasks=tasks,
        cwd=os.getcwd(),
        loop_running=loop_running,
    )


@app.get("/api/agents")
async def get_agents():
    return ["gemini", "aider", "claude", "codex"]


@app.post("/api/tasks")
async def add_task(task: Task):
    with lock_tasks(TASKS_FILE):
        data = load_tasks(TASKS_FILE)
        task_id = generate_task_id()
        new_task = {
            "id": task_id,
            "description": task.description,
            "status": "pending",
            "attempts": 0,
            "outcomes": [],
        }
        data["tasks"].append(new_task)
        save_tasks(TASKS_FILE, data)
    return new_task


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: str, update: TaskUpdate):
    with lock_tasks(TASKS_FILE):
        data = load_tasks(TASKS_FILE)
        target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)
        if not target:
            raise HTTPException(status_code=404, detail="Task not found")

        if target.get("status") == "completed" and update.description:
            raise HTTPException(
                status_code=400, detail="Cannot edit description of a completed task"
            )

        if update.status:
            if update.status == "completed" and target.get("status") != "completed":
                target["completed_at"] = time.time()
            elif update.status != "completed" and target.get("status") == "completed":
                if "completed_at" in target:
                    del target["completed_at"]
            target["status"] = update.status
        if update.description:
            target["description"] = update.description

        save_tasks(TASKS_FILE, data)
    return target


@app.delete("/api/tasks/completed")
async def delete_completed_tasks():
    with lock_tasks(TASKS_FILE):
        data = load_tasks(TASKS_FILE)
        data["tasks"] = [t for t in data["tasks"] if t.get("status") != "completed"]
        save_tasks(TASKS_FILE, data)
    return {"status": "ok"}


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    with lock_tasks(TASKS_FILE):
        data = load_tasks(TASKS_FILE)
        data["tasks"] = [t for t in data["tasks"] if not t["id"].startswith(task_id)]
        save_tasks(TASKS_FILE, data)
    return {"status": "ok"}


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task_endpoint(task_id: str):
    if cancel_task(TASKS_FILE, task_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Task not found")


@app.post("/api/context")
async def update_context(update: ContextUpdate):
    with lock_tasks(TASKS_FILE):
        data = load_tasks(TASKS_FILE)
        data["context"] = update.context
        save_tasks(TASKS_FILE, data)
    return {"status": "ok"}


@app.post("/api/run")
async def run_loop(request: RunRequest):
    # Run lemming run in the background
    cmd = ["lemming", "run"]
    if request.agent:
        cmd.extend(["--agent", request.agent])
    subprocess.Popen(cmd, start_new_session=True)
    return {"status": "started"}


def is_ignored(path: pathlib.Path) -> bool:
    """Check if a path is ignored by .gitignore."""
    try:
        # Use -q for quiet (only exit code), and --no-index to check untracked files
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(path)], capture_output=True
        )
        return result.returncode == 0
    except Exception:
        return False


@app.get("/api/files/{path:path}")
async def get_files_api(path: str):
    base_path = pathlib.Path(os.getcwd()).resolve()
    target_path = (base_path / path).resolve()

    # Security: Ensure target_path is within base_path
    if not str(target_path).startswith(str(base_path)):
        raise HTTPException(status_code=403, detail="Forbidden")

    if is_ignored(target_path):
        raise HTTPException(status_code=403, detail="Access denied by .gitignore")

    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    contents = []
    try:
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
                    "size": stats.st_size if not is_dir else None,
                    "modified": stats.st_mtime,
                }
            )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Sort: directories first, then files, both alphabetically
    contents.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    return {"path": path, "contents": contents}


@app.get("/files")
async def redirect_files():
    return RedirectResponse(url="/files/")


@app.get("/files/{path:path}")
async def serve_files(path: str):
    base_path = pathlib.Path(os.getcwd()).resolve()
    target_path = (base_path / path).resolve()

    # Security: Ensure target_path is within base_path
    if not str(target_path).startswith(str(base_path)):
        raise HTTPException(status_code=403, detail="Forbidden")

    if is_ignored(target_path):
        raise HTTPException(status_code=403, detail="Access denied by .gitignore")

    if target_path.is_dir():
        return FileResponse(web_dir / "files.html")

    elif target_path.is_file():
        return FileResponse(target_path)

    else:
        raise HTTPException(status_code=404, detail="Not found")


# Serve static files
web_dir = pathlib.Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=web_dir), name="static")


@app.get("/")
async def read_index():
    return FileResponse(web_dir / "index.html")
