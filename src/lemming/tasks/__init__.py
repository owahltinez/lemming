"""Task management package: lifecycle, operations, progress, and queries."""

from .. import models, persistence
from .lifecycle import (
    STALE_THRESHOLD as STALE_THRESHOLD,
)
from .lifecycle import (
    cancel_task as cancel_task,
)
from .lifecycle import (
    claim_task as claim_task,
)
from .lifecycle import (
    finish_task_attempt as finish_task_attempt,
)
from .lifecycle import (
    generate_task_id as generate_task_id,
)
from .lifecycle import (
    is_loop_running as is_loop_running,
)
from .lifecycle import (
    is_pid_alive as is_pid_alive,
)
from .lifecycle import (
    mark_task_in_progress as mark_task_in_progress,
)
from .lifecycle import (
    reset_task as reset_task,
)
from .lifecycle import (
    reset_task_logs as reset_task_logs,
)
from .lifecycle import (
    update_heartbeat as update_heartbeat,
)
from .lifecycle import (
    update_run_time as update_run_time,
)
from .operations import (
    add_task as add_task,
)
from .operations import (
    delete_tasks as delete_tasks,
)
from .operations import (
    update_goal as update_goal,
)
from .operations import (
    update_task as update_task,
)
from .progress import add_progress as add_progress
from .queries import (
    get_pending_task as get_pending_task,
)
from .queries import (
    get_project_data as get_project_data,
)

# Re-export persistence functions as part of the package facade
save_tasks = persistence.save_tasks
load_tasks = persistence.load_tasks
lock_tasks = persistence.lock_tasks
acquire_loop_lock = persistence.acquire_loop_lock
release_loop_lock = persistence.release_loop_lock
get_loop_pid = persistence.get_loop_pid
LOOP_LOCK_FILENAME = persistence.LOOP_LOCK_FILENAME

# Re-export models as part of the package facade
TaskNotFoundError = models.TaskNotFoundError
Task = models.Task
TaskStatus = models.TaskStatus
ProjectData = models.ProjectData
RoadmapConfig = models.RoadmapConfig
Roadmap = models.Roadmap
