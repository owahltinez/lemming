import fastapi
import pydantic

from .. import tasks
from .. import paths
from . import context
from . import loop

router = fastapi.APIRouter()


@router.get("/api/data", response_model=tasks.ProjectData)
def get_data(request: fastapi.Request, project: str | None = None):
    tasks_file = context.resolve_tasks_file(request.app.state, project)
    data = tasks.get_project_data(tasks_file)
    data.cwd = str(context.resolve_project_dir(request.app.state, project))
    return data


class AddTaskRequest(pydantic.BaseModel):
    description: str
    runner: str | None = None
    index: int = -1
    parent: str | None = None
    parent_tasks_file: str | None = None


@router.post("/api/tasks")
def add_task(
    request: fastapi.Request, task: AddTaskRequest, project: str | None = None
):
    tasks_file = context.resolve_tasks_file(request.app.state, project)
    new_task = tasks.add_task(
        tasks_file,
        task.description,
        task.runner,
        index=task.index,
        parent=task.parent,
        parent_tasks_file=task.parent_tasks_file,
    )
    loop.start_loop_if_needed(
        request.app.state,
        tasks_file,
        cwd=context.resolve_project_dir(request.app.state, project),
    )
    return new_task


@router.get("/api/tasks/{task_id}", response_model=tasks.Task)
def get_task(request: fastapi.Request, task_id: str, project: str | None = None):
    tasks_file = context.resolve_tasks_file(request.app.state, project)
    data = tasks.load_tasks(tasks_file)
    target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
    if not target:
        raise fastapi.HTTPException(404, "Task not found")
    return target


@router.post("/api/tasks/{task_id}/update")
def update_task(
    request: fastapi.Request, task_id: str, update: dict, project: str | None = None
):
    tasks_file = context.resolve_tasks_file(request.app.state, project)
    status = update.get("status")

    # Validation: require outcomes if completing or failing from the UI,
    # but not if we are just marking a finished task as pending (uncomplete).
    require_outcomes = False
    if status in (
        tasks.TaskStatus.COMPLETED,
        tasks.TaskStatus.FAILED,
        tasks.TaskStatus.PENDING,
    ):
        data = tasks.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if target and target.status not in (
            tasks.TaskStatus.COMPLETED,
            tasks.TaskStatus.FAILED,
            tasks.TaskStatus.CANCELLED,
        ):
            require_outcomes = True

    try:
        updated_task = tasks.update_task(
            tasks_file,
            task_id,
            description=update.get("description"),
            runner=update.get("runner"),
            index=update.get("index"),
            status=status,
            require_outcomes=require_outcomes,
            parent=update.get("parent"),
        )
        return updated_task
    except ValueError as e:
        if "not found" in str(e):
            raise fastapi.HTTPException(404, str(e))
        raise fastapi.HTTPException(400, str(e))


@router.post("/api/tasks/delete-completed")
def delete_completed_tasks(request: fastapi.Request, project: str | None = None):
    tasks.delete_tasks(
        context.resolve_tasks_file(request.app.state, project), completed_only=True
    )
    return {"status": "ok"}


@router.post("/api/tasks/{task_id}/delete")
def delete_task(request: fastapi.Request, task_id: str, project: str | None = None):
    tasks.delete_tasks(
        context.resolve_tasks_file(request.app.state, project), task_id=task_id
    )
    return {"status": "ok"}


@router.post("/api/tasks/{task_id}/cancel")
def cancel_task_endpoint(
    request: fastapi.Request, task_id: str, project: str | None = None
):
    if tasks.cancel_task(
        context.resolve_tasks_file(request.app.state, project), task_id
    ):
        return {"status": "ok"}
    raise fastapi.HTTPException(404, "Task not found")


@router.post("/api/tasks/{task_id}/clear")
def clear_task_endpoint(
    request: fastapi.Request, task_id: str, project: str | None = None
):
    try:
        tasks_file = context.resolve_tasks_file(request.app.state, project)
        tasks.reset_task(tasks_file, task_id)
        return {"status": "ok"}
    except ValueError as e:
        raise fastapi.HTTPException(404, str(e))


@router.get("/api/tasks/{task_id}/log")
def get_task_log(request: fastapi.Request, task_id: str, project: str | None = None):
    log_file = paths.get_log_file(
        context.resolve_tasks_file(request.app.state, project), task_id
    )
    if not log_file.exists():
        return {"log": ""}
    return {"log": log_file.read_text(encoding="utf-8")}
