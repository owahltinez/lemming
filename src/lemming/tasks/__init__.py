from .. import models, persistence
from .lifecycle import (
    STALE_THRESHOLD as STALE_THRESHOLD,
    cancel_task as cancel_task,
    claim_task as claim_task,
    finish_task_attempt as finish_task_attempt,
    generate_task_id as generate_task_id,
    is_loop_running as is_loop_running,
    is_pid_alive as is_pid_alive,
    mark_task_in_progress as mark_task_in_progress,
    reset_task as reset_task,
    reset_task_logs as reset_task_logs,
    update_heartbeat as update_heartbeat,
    update_run_time as update_run_time,
)
from .operations import (
    add_task as add_task,
    delete_tasks as delete_tasks,
    update_context as update_context,
    update_task as update_task,
)
from .outcomes import (
    add_outcome as add_outcome,
    delete_outcome as delete_outcome,
    edit_outcome as edit_outcome,
)
from .queries import (
    get_pending_task as get_pending_task,
    get_project_data as get_project_data,
)

# Re-export persistence functions for backward compatibility
save_tasks = persistence.save_tasks
load_tasks = persistence.load_tasks
lock_tasks = persistence.lock_tasks
acquire_loop_lock = persistence.acquire_loop_lock
release_loop_lock = persistence.release_loop_lock
get_loop_pid = persistence.get_loop_pid
LOOP_LOCK_FILENAME = persistence.LOOP_LOCK_FILENAME

# Re-export models for compatibility
Task = models.Task
TaskStatus = models.TaskStatus
ProjectData = models.ProjectData
RoadmapConfig = models.RoadmapConfig
Roadmap = models.Roadmap
