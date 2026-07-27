"""CLI commands for managing tasks in the roadmap queue."""

import math
import time
import typing

import click

from .. import paths, tasks
from .main import cli


def _format_elapsed_time(seconds: float) -> str:
    """Format elapsed seconds for CLI status output."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"


def _execution_time_rows(
    execution_times: dict[str, float] | None,
) -> list[tuple[str, str]]:
    """Return display labels and durations with the runner first."""
    if not execution_times:
        return []

    components = [
        (component, duration)
        for component, duration in execution_times.items()
        if math.isfinite(duration) and duration > 0
    ]
    components.sort(key=lambda item: item[0] != "runner")
    return [
        (
            component.removeprefix("hook:"),
            _format_elapsed_time(duration),
        )
        for component, duration in components
    ]


def _echo_task_summary(task: tasks.Task, all_tasks: list[tasks.Task]) -> None:
    """Print one task row plus compact supersession lineage."""
    marker_by_status = {
        tasks.TaskStatus.COMPLETED: ("[x]", "green"),
        tasks.TaskStatus.IN_PROGRESS: ("[*]", "cyan"),
        tasks.TaskStatus.CANCELLED: ("[-]", "red"),
        tasks.TaskStatus.SUPERSEDED: ("[~]", "magenta"),
        tasks.TaskStatus.FAILED: ("[!]", "red"),
        tasks.TaskStatus.PENDING: ("[ ]", "yellow"),
    }
    marker, status_color = marker_by_status[task.status]
    click.secho(f"{marker} ", fg=status_color, nl=False)
    parent_str = f" [parent:{task.parent}]" if task.parent else ""
    click.echo(f"({task.id}){parent_str} {task.description}")

    if task.status == tasks.TaskStatus.SUPERSEDED:
        if task.superseded_reason:
            click.echo(f"    Reason: {task.superseded_reason}")
        replacements = [item.id for item in all_tasks if item.parent == task.id]
        if replacements:
            click.echo(f"    Replaced by: {', '.join(replacements)}")


@cli.command(short_help="[description] Add a new task to the queue")
@click.argument("description", required=False)
@click.option(
    "--file",
    "-f",
    type=click.File("r"),
    help="Read description from a file (or - for stdin).",
)
@click.option(
    "--index",
    default=-1,
    help="Zero-based displayed queue position (-1 appends).",
)
@click.option(
    "--runner",
    "runner_name",
    help=(
        "Custom runner to use for this task (overrides the default run runner)."
    ),
)
@click.option(
    "--parent",
    help="ID of the parent task.",
)
@click.option(
    "--parent-tasks-file",
    help="Path to the parent tasks file (optional).",
)
@click.pass_context
def add(
    ctx: click.Context,
    description: str | None,
    file: typing.Optional[typing.TextIO],
    index: int,
    runner_name: str | None,
    parent: str | None,
    parent_tasks_file: str | None,
):
    """Adds a new task to the roadmap queue."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]

    if file:
        if description:
            click.echo("Error: Cannot provide both description and --file.")
            ctx.exit(1)
        description = file.read().strip()
    elif description:
        description = description.strip()

    if not description:
        click.echo("Error: Must provide either description or --file.")
        ctx.exit(1)

    try:
        new_task = tasks.add_task(
            tasks_file,
            description,
            runner_name,
            index=index,
            parent=parent,
            parent_tasks_file=parent_tasks_file,
        )
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)
    task_id = new_task.id

    if verbose:
        click.echo(f"Added task {task_id}: {description}")
    else:
        click.echo(task_id)


@cli.command(short_help="<taskid> Edit an existing task's details")
@click.argument("task_id")
@click.option("--description", help="New description for the task.")
@click.option(
    "--file",
    "-f",
    type=click.File("r"),
    help="Read new description from a file (or - for stdin).",
)
@click.option("--runner", "runner_name", help="New custom runner for the task.")
@click.option(
    "--index",
    type=int,
    help="New zero-based position in the displayed task queue.",
)
@click.option(
    "--parent",
    help="New parent task ID (use empty string to remove).",
)
@click.option(
    "--parent-tasks-file",
    help="New parent tasks file path (use empty string to remove).",
)
@click.pass_context
def edit(
    ctx: click.Context,
    task_id: str,
    description: str | None,
    file: typing.Optional[typing.TextIO],
    runner_name: str | None,
    index: int | None,
    parent: str | None,
    parent_tasks_file: str | None,
):
    """Edits an existing task's description, runner, position, or parent."""
    if file:
        if description:
            click.echo("Error: Cannot provide both description and --file.")
            ctx.exit(1)
        description = file.read().strip()
    elif description:
        description = description.strip()

    if (
        description is None
        and runner_name is None
        and index is None
        and parent is None
        and parent_tasks_file is None
    ):
        click.echo(
            "Error: At least one of --description, --runner, --index,"
            " --parent, or --parent-tasks-file must be provided."
        )
        ctx.exit(1)

    tasks_file = ctx.obj["TASKS_FILE"]

    try:
        target_task = tasks.update_task(
            tasks_file,
            task_id,
            description=description,
            runner=runner_name,
            index=index,
            parent=parent,
            parent_tasks_file=parent_tasks_file,
        )
        click.echo(f"Task {target_task.id} updated.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@cli.command(name="delete", short_help="<taskid> Delete a task from the queue")
@click.argument("task_id", required=False)
@click.option(
    "--all",
    "delete_all",
    is_flag=True,
    help="Delete all tasks and clear the goal.",
)
@click.option(
    "--completed",
    is_flag=True,
    help="Delete terminal task history and its logs.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Explicitly remove a task that has execution history.",
)
@click.pass_context
def delete_task(
    ctx: click.Context,
    task_id: str | None,
    delete_all: bool,
    completed: bool,
    force: bool,
):
    """Deletes one or more tasks from the roadmap."""
    tasks_file = ctx.obj["TASKS_FILE"]

    # Validate argument combinations
    if delete_all and completed:
        click.echo("Error: --all and --completed are mutually exclusive.")
        ctx.exit(1)
    if task_id and (delete_all or completed):
        click.echo("Error: Cannot specify a task ID with --all or --completed.")
        ctx.exit(1)
    if force and not task_id:
        click.echo("Error: --force requires a task ID.")
        ctx.exit(1)
    if not task_id and not delete_all and not completed:
        click.echo("Error: Provide a task ID, or use --all or --completed.")
        ctx.exit(1)

    try:
        removed = tasks.delete_tasks(
            tasks_file,
            task_id=task_id,
            all_tasks=delete_all,
            completed_only=completed,
            force=force,
        )
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)

    if delete_all:
        click.echo(
            "Deleted all tasks, progress, and logs, and cleared the goal."
        )
    elif completed:
        click.echo(f"Deleted {removed} history task(s) and their logs.")
    elif task_id:
        if removed > 0:
            click.echo(
                f"Removed task matching {task_id}; runner log retained "
                "if present."
            )
        else:
            click.echo(f"Error: Task {task_id} not found.")


@cli.command(short_help="<taskid> Replace a task while keeping its history")
@click.argument("task_id")
@click.option(
    "--reason",
    required=True,
    help="Why the task was replaced (for example, split after timeout).",
)
@click.pass_context
def supersede(ctx: click.Context, task_id: str, reason: str):
    """Marks a task as superseded without deleting its execution record."""
    try:
        target = tasks.supersede_task(ctx.obj["TASKS_FILE"], task_id, reason)
        click.echo(f"Task {target.id} marked as superseded: {reason.strip()}")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@cli.command(short_help="<taskid> Show the goal and task details")
@click.argument("task_id", required=False)
@click.pass_context
def status(ctx: click.Context, task_id: str | None):
    """Displays the roadmap status or details for a specific task."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    project_data = tasks.get_project_data(tasks_file)

    if not task_id:
        if project_data.loop_running:
            loop_state = "Running"
            loop_color = "green"
        else:
            loop_state = "Idle"
            loop_color = "cyan"

        click.secho(f"Loop Status: {loop_state}", fg=loop_color, bold=True)
        click.echo()

        if verbose:
            click.secho("=== Long-Term Goal ===", fg="cyan", bold=True)
            click.echo(project_data.goal or "No goal set.")

        if not project_data.tasks:
            if verbose:
                click.echo("No tasks found.")
            return

        terminal_statuses = {
            tasks.TaskStatus.COMPLETED,
            tasks.TaskStatus.FAILED,
            tasks.TaskStatus.CANCELLED,
            tasks.TaskStatus.SUPERSEDED,
        }
        queue = [
            task
            for task in project_data.tasks
            if task.status not in terminal_statuses
        ]
        history = [
            task
            for task in project_data.tasks
            if task.status in terminal_statuses
        ]
        visible_history = (
            history
            if verbose
            else [
                task
                for task in history
                if task.status
                in (tasks.TaskStatus.FAILED, tasks.TaskStatus.SUPERSEDED)
            ]
        )

        queue_heading = "\n=== Queue ===" if verbose else "Queue:"
        click.secho(queue_heading, fg="cyan", bold=True)
        if queue:
            for task in queue:
                _echo_task_summary(task, project_data.tasks)
        else:
            click.echo("No active tasks.")

        if visible_history:
            history_heading = "\n=== History ===" if verbose else "\nHistory:"
            click.secho(history_heading, fg="magenta", bold=True)
            for task in visible_history:
                _echo_task_summary(task, project_data.tasks)

        hidden_count = len(history) - len(visible_history)
        if hidden_count:
            click.echo(
                f"({hidden_count} completed/cancelled history task(s) hidden; "
                "use --verbose to show)"
            )
        return

    try:
        target = tasks.resolve_task(project_data.tasks, task_id)
    except tasks.TaskNotFoundError:
        try:
            retained_log = tasks.resolve_log_file(tasks_file, task_id)
        except tasks.TaskNotFoundError:
            click.echo(f"Error: Task {task_id} not found.")
            return
        except tasks.AmbiguousTaskIdError as e:
            click.echo(f"Error: {e}")
            return

        retained_id = retained_log.name.removesuffix("-runner.log")
        click.echo(
            f"Task {retained_id} was removed; runner log retained at "
            f"{retained_log}"
        )
        return
    except tasks.AmbiguousTaskIdError as e:
        click.echo(f"Error: {e}")
        return

    if project_data.loop_running:
        loop_state = "Running"
        loop_color = "green"
    else:
        loop_state = "Idle"
        loop_color = "cyan"

    click.secho(f"Loop Status:   {loop_state}", fg=loop_color, bold=True)
    click.secho(f"Task ID:       {target.id}", bold=True)
    status_str = str(target.status)
    if (
        target.status == tasks.TaskStatus.IN_PROGRESS
        and target.requested_status
    ):
        status_str += f" ({target.requested_status} requested, hooks running)"
    click.echo(f"Status:        {status_str}")
    click.echo(f"Description:   {target.description}")
    if target.parent:
        parent = next(
            (task for task in project_data.tasks if task.id == target.parent),
            None,
        )
        parent_context = (
            f" [{parent.status}] {parent.description}" if parent else ""
        )
        click.echo(f"Parent:        {target.parent}{parent_context}")
    if target.runner:
        click.echo(f"Custom Runner: {target.runner}")
    click.echo(f"Attempts:      {target.attempts}")
    if target.created_at:
        created_time = time.strftime(
            "%Y-%m-%d %H:%M:%S %Z", time.localtime(target.created_at)
        )
        click.echo(f"Created At:    {created_time}")

    log_parts = []
    if paths.get_log_file(tasks_file, target.id).exists():
        log_parts.append("runner (includes hooks)")
    click.echo(
        f"Logs:          {', '.join(log_parts) if log_parts else 'None'}"
    )

    if target.completed_at:
        comp_time = time.strftime(
            "%Y-%m-%d %H:%M:%S %Z", time.localtime(target.completed_at)
        )
        click.echo(f"Completed At:  {comp_time}")
    if target.superseded_at:
        superseded_time = time.strftime(
            "%Y-%m-%d %H:%M:%S %Z",
            time.localtime(target.superseded_at),
        )
        click.echo(f"Superseded At: {superseded_time}")
    if target.superseded_reason:
        click.echo(f"Reason:         {target.superseded_reason}")

    replacements = [
        task for task in project_data.tasks if task.parent == target.id
    ]
    if replacements:
        click.echo("Replaced By:")
        for replacement in replacements:
            click.echo(
                f"  {replacement.id} [{replacement.status}] "
                f"{replacement.description}"
            )
    run_time = target.run_time
    if target.status == tasks.TaskStatus.IN_PROGRESS and target.last_started_at:
        run_time += time.time() - target.last_started_at

    if run_time > 0:
        click.echo(f"Run Time:      {_format_elapsed_time(run_time)}")

    execution_rows = _execution_time_rows(target.execution_times)
    if execution_rows:
        label_width = max(len(label) for label, _ in execution_rows)
        for label, duration in execution_rows:
            click.echo(f"  {label:<{label_width}}  {duration}")

    if target.progress:
        click.secho("\n--- Progress ---", fg="magenta", bold=True)
        for i, entry in enumerate(target.progress):
            click.echo(f"[{i}] {entry}")


@cli.command(short_help="[<taskid>] Print a task's log to stdout")
@click.argument("task_id", required=False)
@click.pass_context
def logs(ctx: click.Context, task_id: str | None):
    """Prints the execution log for a task to stdout.

    If no task_id is provided, it defaults to the currently running task or
    the most recently finished one.

    Note: Orchestrator hooks are appended to the main 'runner' log.
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    data = tasks.load_tasks(tasks_file)

    target = None
    log_file = None
    if task_id:
        try:
            target = tasks.resolve_task(data.tasks, task_id)
        except tasks.TaskNotFoundError:
            try:
                log_file = tasks.resolve_log_file(tasks_file, task_id)
            except (tasks.TaskNotFoundError, tasks.AmbiguousTaskIdError) as e:
                click.echo(f"Error: {e}")
                ctx.exit(1)
        except tasks.AmbiguousTaskIdError as e:
            click.echo(f"Error: {e}")
            ctx.exit(1)
    else:
        # Try to find an active task
        target = next(
            (t for t in data.tasks if t.status == tasks.TaskStatus.IN_PROGRESS),
            None,
        )
        if not target:
            # Fall back to the most recently finished task.
            finished = [
                task
                for task in data.tasks
                if task.status
                in (
                    tasks.TaskStatus.COMPLETED,
                    tasks.TaskStatus.FAILED,
                    tasks.TaskStatus.CANCELLED,
                    tasks.TaskStatus.SUPERSEDED,
                )
            ]
            if finished:
                target = max(
                    finished,
                    key=lambda task: (
                        task.completed_at
                        or task.superseded_at
                        or task.created_at
                        or 0
                    ),
                )

    if not target and log_file is None:
        click.echo("Error: No active or recently finished task found.")
        ctx.exit(1)

    if log_file is None:
        log_file = paths.get_log_file(tasks_file, target.id)
    if not log_file.exists():
        click.echo(f"No log for task {target.id}.")
        ctx.exit(1)

    # Highlight separators for better readability in the terminal
    content = log_file.read_text(encoding="utf-8")
    for line in content.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            click.secho(line, fg="cyan", bold=True)
        else:
            click.echo(line)


@cli.command(short_help="<taskid> Mark a task as completed")
@click.argument("task_id")
@click.option(
    "--force",
    is_flag=True,
    help="Finalize an in-progress task immediately, without running hooks.",
)
@click.pass_context
def complete(ctx: click.Context, task_id: str, force: bool):
    """Marks a task as completed (requires at least one progress entry)."""
    tasks_file = ctx.obj["TASKS_FILE"]

    try:
        data = tasks.load_tasks(tasks_file)
        current_task = tasks.resolve_task(data.tasks, task_id)
        task_is_active = tasks.is_task_active(current_task, time.time())
        if (
            current_task.status == tasks.TaskStatus.IN_PROGRESS
            and current_task.progress
            and not force
            and not task_is_active
            and not tasks.is_loop_running(tasks_file)
        ):
            raise ValueError(
                f"Task {current_task.id} is in progress, but no active runner "
                "or loop can finalize it. Use --force to complete it without "
                "running hooks, or reset it to retry."
            )

        target_task = tasks.update_task(
            tasks_file,
            task_id,
            status=tasks.TaskStatus.COMPLETED,
            require_progress=True,
            force=force,
        )
        if target_task.requested_status == tasks.TaskStatus.COMPLETED:
            click.echo(
                f"Task {target_task.id} completion requested; "
                "finalization hooks are pending."
            )
        else:
            click.echo(f"Task {target_task.id} marked as completed.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@cli.command(short_help="<taskid> Mark a completed task as pending")
@click.argument("task_id")
@click.pass_context
def uncomplete(ctx: click.Context, task_id: str):
    """Unmarks a completed task, moving it back to 'pending' status."""
    tasks_file = ctx.obj["TASKS_FILE"]
    try:
        target_task = tasks.update_task(
            tasks_file, task_id, status=tasks.TaskStatus.PENDING
        )
        click.echo(f"Task {target_task.id} marked as pending.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@cli.command(short_help="<taskid> Record a task failure")
@click.argument("task_id")
@click.pass_context
def fail(ctx: click.Context, task_id: str):
    """Marks a task as failed (requires recorded progress)."""
    tasks_file = ctx.obj["TASKS_FILE"]
    try:
        target_task = tasks.update_task(
            tasks_file,
            task_id,
            status=tasks.TaskStatus.FAILED,
            require_progress=True,
        )
        click.echo(f"Task {target_task.id} marked as failed.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@cli.command(short_help="<taskid> Cancel a pending or active task")
@click.argument("task_id")
@click.pass_context
def cancel(ctx: click.Context, task_id: str):
    """Cancels a task, stopping its runner if it is active."""
    tasks_file = ctx.obj["TASKS_FILE"]
    if tasks.cancel_task(tasks_file, task_id):
        click.echo(f"Task {task_id} cancelled.")
    else:
        click.echo(f"Error: Task {task_id} not found or not in progress.")
        ctx.exit(1)


@cli.command(short_help="<taskid> Clear a task's attempts and progress")
@click.argument("task_id")
@click.pass_context
def reset(ctx: click.Context, task_id: str):
    """Clears a task's attempts, progress, and logs."""
    tasks_file = ctx.obj["TASKS_FILE"]
    try:
        target_task = tasks.reset_task(tasks_file, task_id)
        click.echo(
            f"Task {target_task.id} attempts, progress, and logs cleared."
        )
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)
