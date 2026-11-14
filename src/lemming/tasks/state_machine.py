"""Task state machine: the allowed status changes and their workspace effects.

Every status change goes through set_status, which rejects moves the table
below does not list. Each active status keeps one workspace checkpoint, and
settle returns a task to it whenever its owner stops: a run goes back to
pending at its pre_tree, a review keeps the agent's work at its runner_tree.
"""

import pathlib
import time

from .. import models, paths, persistence, snapshots

S = models.TaskStatus

# Terminal statuses only reopen, or are relabelled by hand.
_NEXT: dict[S, frozenset[S]] = {
    S.PENDING: frozenset(
        {S.RUNNING, S.FAILING, S.COMPLETED, S.FAILED, S.CANCELLED, S.SUPERSEDED}
    ),
    S.RUNNING: frozenset(
        {
            S.PENDING,
            S.COMPLETING,
            S.FAILING,
            S.COMPLETED,
            S.FAILED,
            S.CANCELLED,
            S.SUPERSEDED,
        }
    ),
    S.COMPLETING: frozenset(
        {S.PENDING, S.COMPLETED, S.CANCELLED, S.SUPERSEDED}
    ),
    S.FAILING: frozenset({S.PENDING, S.FAILED, S.CANCELLED, S.SUPERSEDED}),
    **{
        status: frozenset({S.PENDING, S.COMPLETED, S.FAILED} - {status})
        for status in models.TERMINAL_STATUSES
    },
}


class TransitionError(ValueError):
    """Raised for a status change the state machine does not allow."""


def update_run_time(task: models.Task, end_time: float | None = None) -> None:
    """Accumulates the execution run time for a given task.

    Args:
        task: The Task to update.
        end_time: Optional end timestamp (defaults to current time).
    """
    if task.last_started_at is not None:
        end = end_time or time.time()
        duration = end - task.last_started_at
        task.run_time += duration
        task.last_started_at = None


def release(task: models.Task, end_time: float | None = None) -> None:
    """Ends the current owner's claim on a task, stopping its clock."""
    update_run_time(task, end_time)
    task.pid = None
    task.last_heartbeat = None
    task.active_execution_component = None
    task.active_execution_started_at = None


def set_status(task: models.Task, status: S, now: float | None = None) -> None:
    """Moves a task to a new status, keeping its bookkeeping consistent.

    Args:
        task: The task to update in place.
        status: The status to move it to; the current one is a no-op.
        now: When the change happened, defaulting to the current time.

    Raises:
        TransitionError: If the table does not allow the move.
    """
    if status == task.status:
        return
    if status not in _NEXT[task.status]:
        raise TransitionError(
            f"Task {task.id} cannot go from {task.status} to {status}."
        )

    now = now or time.time()
    was_active = task.status in models.ACTIVE_STATUSES
    task.status = status
    if status in models.ACTIVE_STATUSES:
        if not was_active:
            task.started_at = task.started_at or now
            task.last_started_at = now
            task.last_heartbeat = now
            task.active_execution_component = None
            task.active_execution_started_at = None
        if status == S.RUNNING:
            task.attempts += 1
            # A new attempt must not inherit the last one's rejection.
            task.rejection = None
        return

    if was_active:
        release(task, now)
    task.rejection = None
    if status == S.PENDING:
        task.completed_at = None
        task.superseded_at = None
        task.superseded_reason = None
    elif status != S.SUPERSEDED:
        task.completed_at = now


def write_patch_artifact(
    tasks_file: pathlib.Path,
    task_id: str,
    filename: str,
    pre_tree: str | None,
    post_tree: str | None,
) -> None:
    """Writes the diff between two trees into the task's artifacts directory."""
    if not pre_tree or not post_tree or pre_tree == post_tree:
        return
    patch_text = snapshots.diff_trees(tasks_file, pre_tree, post_tree)
    if not patch_text:
        return
    artifacts_dir = paths.get_artifacts_dir(tasks_file, task_id)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    # A patch may be the only copy of reverted work, so never overwrite one.
    base = target = artifacts_dir / filename
    n = 1
    while target.exists():
        n += 1
        target = base.with_name(f"{base.stem}-{n}{base.suffix}")
    target.write_text(patch_text, encoding="utf-8", errors="surrogateescape")


def rewind(
    tasks_file: pathlib.Path,
    task_id: str,
    working_dir: pathlib.Path,
    tree: str | None,
    head: str | None,
    patch_name: str,
) -> None:
    """Restores the workspace to a checkpoint, saving what it drops as a patch.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: Task whose artifacts directory receives the patch.
        working_dir: Workspace to restore.
        tree: Checkpoint tree; None means there is nothing to restore.
        head: Host Git HEAD to restore along with the tree, if any.
        patch_name: Artifact filename for the discarded changes.
    """
    if tree is None:
        return
    current = snapshots.snapshot_tree(tasks_file, working_dir)
    write_patch_artifact(tasks_file, task_id, patch_name, tree, current)
    if current != tree or (
        head is not None and snapshots.head_commit(working_dir) != head
    ):
        snapshots.restore_tree(
            tasks_file,
            working_dir,
            tree,
            current_tree=current,
            target_head=head,
        )


def _find(data: models.Roadmap, task_id: str) -> models.Task | None:
    return next((t for t in data.tasks if t.id == task_id), None)


def _attempt_patch(task: models.Task) -> str:
    return f"attempt-{max(1, task.attempts)}.patch"


def settle(
    tasks_file: pathlib.Path,
    task_id: str,
    working_dir: pathlib.Path,
    *,
    counted: bool = True,
    released_at: float | None = None,
) -> models.Task | None:
    """Returns a task to its status's checkpoint once its process stops.

    A running task goes back to pending at its pre_tree. A completing task
    keeps the agent's work: its runner_tree is taken the first time and
    restored after that, so only the reviews start over. A failing or
    cancelled task drops the attempt's changes.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task to settle.
        working_dir: Workspace the task ran in.
        counted: Whether a stopped run uses up one of the task's attempts.
        released_at: When the owner stopped, if it gave up the task;
            otherwise the calling loop carries on with it.

    Returns:
        The settled task, or None if it no longer exists.
    """
    task = _find(persistence.load_tasks(tasks_file), task_id)
    if task is None:
        return None
    runner_tree = task.runner_tree
    if task.status == S.COMPLETING:
        if runner_tree is None:
            runner_tree = snapshots.snapshot_tree(tasks_file, working_dir)
        else:
            rewind(
                tasks_file,
                task_id,
                working_dir,
                runner_tree,
                None,
                "interrupted-review.patch",
            )
    elif task.status in (S.RUNNING, S.FAILING, S.CANCELLED):
        patch = (
            "cancelled.patch"
            if task.status == S.CANCELLED
            else _attempt_patch(task)
        )
        rewind(
            tasks_file,
            task_id,
            working_dir,
            task.pre_tree,
            task.pre_head,
            patch,
        )

    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = _find(data, task_id)
        if target is None or target.status != task.status:
            return target
        target.runner_tree = (
            runner_tree if target.status == S.COMPLETING else None
        )
        target.hook_trees = None
        if target.status == S.CANCELLED:
            target.pre_tree = None
            target.pre_head = None
        elif target.status == S.RUNNING:
            if not counted:
                target.attempts = max(0, target.attempts - 1)
            set_status(target, S.PENDING, released_at)
        elif released_at is not None:
            release(target, released_at)
        elif target.status in models.ACTIVE_STATUSES:
            # The runner is gone; each hook records its own pid. Never the
            # caller's, which a cancel would kill along with its group.
            target.pid = None
            target.last_heartbeat = time.time()
        persistence.save_tasks(tasks_file, data)
        return target


def conclude(
    tasks_file: pathlib.Path,
    task_id: str,
    working_dir: pathlib.Path,
    accepted: bool,
) -> models.Task | None:
    """Ends a review, applying the outcome it was heading for or retrying.

    A rejected completion goes back to pending at its pre_tree, keeping its
    attempt count. A task a hook already moved elsewhere is left alone.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the reviewed task.
        working_dir: Workspace the task ran in.
        accepted: Whether the reviews let the outcome stand.

    Returns:
        The task, or None if it no longer exists.
    """
    task = _find(persistence.load_tasks(tasks_file), task_id)
    if task is None or task.status not in (S.COMPLETING, S.FAILING):
        return task
    outcome = task.status.outcome if accepted else S.PENDING
    post_tree = None
    if outcome == S.COMPLETED:
        post_tree = snapshots.snapshot_tree(tasks_file, working_dir)
    elif outcome == S.PENDING:
        rewind(
            tasks_file,
            task_id,
            working_dir,
            task.pre_tree,
            task.pre_head,
            _attempt_patch(task),
        )

    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = _find(data, task_id)
        if target is None or target.status != task.status:
            return target
        if outcome == S.COMPLETED:
            target.post_tree = post_tree
            target.diff_summary = (
                snapshots.summarize_diff(tasks_file, target.pre_tree, post_tree)
                if target.pre_tree and post_tree
                else None
            )
        elif outcome == S.PENDING:
            target.runner_tree = None
            target.hook_trees = None
        set_status(target, outcome)
        persistence.save_tasks(tasks_file, data)
        return target
