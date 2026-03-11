import os
import pathlib
import subprocess
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .core import (
    get_default_tasks_file,
    generate_task_id,
    load_tasks,
    save_tasks,
    lock_tasks,
    cancel_task,
)

app = FastAPI()

# Shared state (simplified for this POC)
TASKS_FILE = get_default_tasks_file()

class Task(BaseModel):
    id: Optional[str] = None
    description: str
    status: str = "pending"
    attempts: int = 0
    lessons: List[str] = []
    agent: Optional[str] = None
    last_heartbeat: Optional[float] = None
    pid: Optional[int] = None

class ProjectData(BaseModel):
    context: str
    tasks: List[Task]

class ContextUpdate(BaseModel):
    context: str

class RunRequest(BaseModel):
    agent: Optional[str] = "gemini"

class TaskUpdate(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = None

@app.get("/api/data", response_model=ProjectData)
async def get_data():
    return load_tasks(TASKS_FILE)
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
            "lessons": [],
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

        if update.status:
            target["status"] = update.status
        if update.description:
            target["description"] = update.description

        save_tasks(TASKS_FILE, data)
    return target

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

# Serve static files
web_dir = pathlib.Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=web_dir), name="static")

@app.get("/")
async def read_index():
    return FileResponse(web_dir / "index.html")
