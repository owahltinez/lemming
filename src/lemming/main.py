import os
import pathlib
import random
import time

import click

from . import runner
from . import paths
from . import tasks


@click.group()
@click.option(
    "--tasks-file",
    type=click.Path(path_type=pathlib.Path),
    help="Path to the tasks file (defaults to ./tasks.yml or project-isolated tasks in ~/.local/lemming/<hash>/).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show verbose output.",
)
@click.pass_context
def cli(ctx: click.Context, tasks_file: pathlib.Path | None, verbose: bool):
    """Lemming: An autonomous, iterative task runner for AI agents.

    Lemming orchestrates AI coding agents by walking through a structured `tasks.yml` file.
    It manages the context, tracks task attempts, and records technical outcomes.
    """
    ctx.ensure_object(dict)
    if tasks_file is None:
        tasks_file = paths.get_default_tasks_file()
    ctx.obj["TASKS_FILE"] = tasks_file.resolve()
    ctx.obj["VERBOSE"] = verbose


@cli.command(short_help="<description> Add a new task to the queue")
@click.argument("description")
@click.option(
    "--index",
    default=-1,
    help="Index to insert the task at (defaults to -1, the end).",
)
@click.option(
    "--runner",
    "runner_name",
    help="Custom runner to use for this task (overrides the default run runner).",
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
    description: str,
    index: int,
    runner_name: str | None,
    parent: str | None,
    parent_tasks_file: str | None,
):
    """Adds a new task to the roadmap queue.

    Args:
        description: A text description of the task to perform.
        index: The position in the roadmap to insert the task.
        runner_name: An optional custom runner to use for this specific task.
        parent: Optional parent task ID.
        parent_tasks_file: Optional parent tasks file path.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]

    new_task = tasks.add_task(
        tasks_file,
        description,
        runner_name,
        index=index,
        parent=parent,
        parent_tasks_file=parent_tasks_file,
    )
    task_id = new_task.id

    if verbose:
        click.echo(f"Added task {task_id}: {description}")
    else:
        click.echo(task_id)


@cli.command(short_help="<taskid> Edit an existing task's details")
@click.argument("task_id")
@click.option("--description", help="New description for the task.")
@click.option("--runner", "runner_name", help="New custom runner for the task.")
@click.option("--index", type=int, help="New index in the task queue.")
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
    runner_name: str | None,
    index: int | None,
    parent: str | None,
    parent_tasks_file: str | None,
):
    """Edits an existing task's description, preferred runner, position, or parent.

    Args:
        task_id: The ID of the task to update.
        description: The new description (optional).
        runner_name: The new preferred runner (optional).
        index: The new position in the roadmap (optional).
        parent: The new parent task ID (optional).
        parent_tasks_file: The new parent tasks file path (optional).
    """
    if (
        description is None
        and runner_name is None
        and index is None
        and parent is None
        and parent_tasks_file is None
    ):
        click.echo(
            "Error: At least one of --description, --runner, --index, --parent, or --parent-tasks-file must be provided."
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
    "--all", "delete_all", is_flag=True, help="Delete all tasks and clear context."
)
@click.option("--completed", is_flag=True, help="Delete completed tasks only.")
@click.pass_context
def delete_task(
    ctx: click.Context, task_id: str | None, delete_all: bool, completed: bool
):
    """Deletes one or more tasks from the roadmap.

    Args:
        task_id: The ID of the specific task to delete.
        delete_all: If set, clears the entire roadmap and project context.
        completed: If set, deletes all tasks marked as 'completed'.
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    # Validate argument combinations
    if delete_all and completed:
        click.echo("Error: --all and --completed are mutually exclusive.")
        ctx.exit(1)
    if task_id and (delete_all or completed):
        click.echo("Error: Cannot specify a task ID with --all or --completed.")
        ctx.exit(1)
    if not task_id and not delete_all and not completed:
        click.echo("Error: Provide a task ID, or use --all or --completed.")
        ctx.exit(1)

    removed = tasks.delete_tasks(
        tasks_file, task_id=task_id, all_tasks=delete_all, completed_only=completed
    )

    if delete_all:
        click.echo("Deleted all tasks, outcomes, and logs, and cleared context.")
    elif completed:
        click.echo(f"Deleted {removed} completed task(s) and their logs.")
    elif task_id:
        if removed > 0:
            click.echo(f"Removed task(s) matching {task_id} and their logs")
        else:
            click.echo(f"Error: Task {task_id} not found.")


@cli.command(short_help="<taskid> Show context and task details")
@click.argument("task_id", required=False)
@click.pass_context
def status(ctx: click.Context, task_id: str | None):
    """Displays the roadmap status or details for a specific task.

    Args:
        task_id: Optional ID of the task to inspect in detail.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    project_data = tasks.get_project_data(tasks_file)

    if not task_id:
        if verbose:
            click.secho("=== Project Context ===", fg="cyan", bold=True)
            click.echo(project_data.context or "No context set.")
            click.secho("\n=== Tasks ===", fg="cyan", bold=True)

        if not project_data.tasks:
            if verbose:
                click.echo("No tasks found.")
            return

        for t in project_data.tasks:
            if not verbose and t.status == "completed":
                continue

            if t.status == "completed":
                marker = "[x]"
                status_color = "green"
            elif t.status == "in_progress":
                marker = "[*]"
                status_color = "cyan"
            else:
                marker = "[ ]"
                status_color = "yellow"

            click.secho(f"{marker} ", fg=status_color, nl=False)
            parent_str = ""
            if t.parent:
                parent_str = f" [parent:{t.parent}]"
            click.echo(f"({t.id}){parent_str} {t.description}")

        if not verbose:
            completed_count = sum(
                1 for t in project_data.tasks if t.status == "completed"
            )
            if completed_count > 0:
                click.echo(f"({completed_count} completed tasks hidden)")
        return

    target = next((t for t in project_data.tasks if t.id.startswith(task_id)), None)

    if not target:
        click.echo(f"Error: Task {task_id} not found.")
        return

    click.secho(f"Task ID:     {target.id}", bold=True)
    click.echo(f"Status:      {target.status}")
    click.echo(f"Description: {target.description}")
    if target.parent:
        click.echo(f"Parent:      {target.parent}")
    if target.runner:
        click.echo(f"Custom Runner: {target.runner}")
    click.echo(f"Attempts:    {target.attempts}")

    log_parts = []
    if paths.get_log_file(tasks_file, target.id).exists():
        log_parts.append("runner (includes hooks)")
    click.echo(f"Logs:        {', '.join(log_parts) if log_parts else 'None'}")

    if target.completed_at:
        comp_time = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(target.completed_at)
        )
        click.echo(f"Completed At: {comp_time}")
    run_time = target.run_time
    if target.status == "in_progress" and target.last_started_at:
        run_time += time.time() - target.last_started_at

    if run_time > 0:
        if run_time < 60:
            rt_str = f"{run_time:.1f}s"
        else:
            rt_str = f"{int(run_time // 60)}m {int(run_time % 60)}s"
        click.echo(f"Run Time:     {rt_str}")

    if target.outcomes:
        click.secho("\n--- Outcomes ---", fg="magenta", bold=True)
        for i, outcome in enumerate(target.outcomes):
            click.echo(f"[{i}] {outcome}")


@cli.command(short_help="[<taskid>] Print a task's log to stdout")
@click.argument("task_id", required=False)
@click.pass_context
def logs(ctx: click.Context, task_id: str | None):
    """Prints the execution log for a task to stdout.

    If no task_id is provided, it defaults to the currently running task or
    the most recently completed one.

    Note: Orchestrator hooks are appended to the main 'runner' log.
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    data = tasks.load_tasks(tasks_file)

    target = None
    if task_id:
        target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
        if not target:
            click.echo(f"Error: Task {task_id} not found.")
            ctx.exit(1)
    else:
        # Try to find an active task
        target = next((t for t in data.tasks if t.status == "in_progress"), None)
        if not target:
            # Fall back to the most recently completed task
            completed = [t for t in data.tasks if t.status == "completed"]
            if completed:
                target = sorted(completed, key=lambda x: x.completed_at or 0)[-1]

    if not target:
        click.echo("Error: No active or recently completed task found.")
        ctx.exit(1)

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


@cli.command(short_help="[<text>] View or set the project context")
@click.argument("context_text", required=False)
@click.option(
    "--file",
    "-f",
    type=click.Path(exists=True, path_type=pathlib.Path),
    help="Read context from a file.",
)
@click.pass_context
def context(ctx: click.Context, context_text: str | None, file: pathlib.Path | None):
    """Sets or displays the global project-wide context and rules.

    Args:
        context_text: The context string to set (optional).
        file: A file path to read the context from (optional).
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    if file:
        tasks.update_context(tasks_file, file.read_text(encoding="utf-8"))
        click.echo("Project context updated.")
    elif context_text:
        tasks.update_context(tasks_file, context_text)
        click.echo("Project context updated.")
    else:
        data = tasks.load_tasks(tasks_file)
        click.echo(data.context or "No context set.")


@cli.command(short_help="<taskid> Mark a task as completed")
@click.argument("task_id")
@click.pass_context
def complete(ctx: click.Context, task_id: str):
    """Marks a task as completed (requires at least one recorded outcome).

    Args:
        task_id: The ID of the task to mark as completed.
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    try:
        target_task = tasks.update_task(
            tasks_file, task_id, status="completed", require_outcomes=True
        )
        click.echo(f"Task {target_task.id} marked as completed.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@cli.command(short_help="<taskid> Mark a completed task as pending")
@click.argument("task_id")
@click.pass_context
def uncomplete(ctx: click.Context, task_id: str):
    """Unmarks a completed task, moving it back to 'pending' status.

    Args:
        task_id: The ID of the task to uncomplete.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    try:
        target_task = tasks.update_task(tasks_file, task_id, status="pending")
        click.echo(f"Task {target_task.id} marked as pending.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


class OutcomeGroup(click.Group):
    """Custom group to handle `lemming outcome <id> <text>` syntax."""

    def parse_args(self, ctx, args):
        # If the first argument is not a known command and not an option,
        # we infer 'list' if one argument is provided, or 'add' if more.
        if (
            args
            and args[0] not in self.commands
            and args[0] not in ("help", "--help")
            and not args[0].startswith("-")
        ):
            if len(args) == 1:
                args.insert(0, "list")
            else:
                args.insert(0, "add")
        return super().parse_args(ctx, args)


@cli.group(cls=OutcomeGroup, short_help="Manage task outcomes")
def outcome():
    """Manages technical outcomes or findings for specific tasks."""
    pass


@outcome.command(name="add", short_help="<taskid> <text> Add an outcome")
@click.argument("task_id")
@click.argument("text")
@click.pass_context
def outcome_add(ctx: click.Context, task_id: str, text: str):
    """Records a technical outcome or finding for a specific task.

    Args:
        task_id: The ID of the task.
        text: The technical detail or outcome to record.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    try:
        target_task = tasks.add_outcome(tasks_file, task_id, text)
        click.echo(f"Outcome added to task {target_task.id}.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@outcome.command(name="delete", short_help="<taskid> <index> Delete an outcome")
@click.argument("task_id")
@click.argument("index", type=int)
@click.pass_context
def outcome_delete(ctx: click.Context, task_id: str, index: int):
    """Deletes an outcome from a task by its index (starting from 0).

    Args:
        task_id: The ID of the task.
        index: The index of the outcome to delete.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    try:
        target_task = tasks.delete_outcome(tasks_file, task_id, index)
        click.echo(f"Outcome {index} deleted from task {target_task.id}.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@outcome.command(name="edit", short_help="<taskid> <index> <new_text> Edit an outcome")
@click.argument("task_id")
@click.argument("index", type=int)
@click.argument("text")
@click.pass_context
def outcome_edit(ctx: click.Context, task_id: str, index: int, text: str):
    """Edits an existing outcome for a task by its index (starting from 0).

    Args:
        task_id: The ID of the task.
        index: The index of the outcome to edit.
        text: The new outcome text.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    try:
        target_task = tasks.edit_outcome(tasks_file, task_id, index, text)
        click.echo(f"Outcome {index} updated for task {target_task.id}.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@outcome.command(name="list", short_help="<taskid> List outcomes for a task")
@click.argument("task_id")
@click.pass_context
def outcome_list(ctx: click.Context, task_id: str):
    """Lists all outcomes for a specific task with their indices.

    Args:
        task_id: The ID of the task.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    data = tasks.load_tasks(tasks_file)
    target = next((t for t in data.tasks if t.id.startswith(task_id)), None)

    if not target:
        click.echo(f"Error: Task {task_id} not found.")
        ctx.exit(1)

    if not target.outcomes:
        click.echo(f"No outcomes for task {target.id}.")
        return

    click.secho(f"Outcomes for task {target.id}:", bold=True)
    for i, o in enumerate(target.outcomes):
        click.echo(f"[{i}] {o}")


@cli.group(name="config", short_help="Manage project configuration")
def config_group():
    """Manages the configuration for the roadmap execution loop."""
    pass


@config_group.command(name="list")
@click.pass_context
def config_list(ctx: click.Context):
    """Shows the current project configuration."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = tasks.load_tasks(tasks_file)
    c = data.config

    click.secho(f"Configuration for {tasks_file}:", bold=True)
    click.echo(f"  Runner:        {c.runner}")
    click.echo(f"  Retries:       {c.retries}")
    click.echo(
        f"  Hooks:         {', '.join(c.hooks) if c.hooks is not None else '(all)'}"
    )


@config_group.command(name="set")
@click.argument(
    "key",
    type=click.Choice(["runner", "retries"]),
)
@click.argument("value")
@click.pass_context
def config_set(ctx: click.Context, key: str, value: str):
    """Sets a configuration value.

    Examples:
      lemming config set runner aider
      lemming config set retries 5
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    data = tasks.load_tasks(tasks_file)

    if key == "runner":
        data.config.runner = value
    elif key == "retries":
        try:
            data.config.retries = int(value)
        except ValueError:
            raise click.UsageError(f"Value for {key} must be an integer.")

    tasks.save_tasks(tasks_file, data)
    click.echo(f"Updated {key} to {value}")


@cli.group(name="hooks", short_help="Manage orchestrator hooks")
def hooks_group():
    """Manages orchestrator hooks."""
    pass


@hooks_group.command(name="list")
@click.pass_context
def hooks_list(ctx: click.Context):
    """Lists available orchestrator hooks."""
    tasks_file = ctx.obj["TASKS_FILE"]
    available = runner.list_hooks(tasks_file)

    data = tasks.load_tasks(tasks_file)
    active = set(data.config.hooks) if data.config.hooks is not None else set(available)

    click.secho("Available orchestrator hooks:", bold=True)
    for h in available:
        status = "[active]" if h in active else ""
        click.echo(f"  - {h:20} {status}")


@hooks_group.command(name="enable")
@click.argument("names", nargs=-1, required=True)
@click.pass_context
def hooks_enable(ctx: click.Context, names: tuple[str, ...]):
    """Enables one or more orchestrator hooks."""
    tasks_file = ctx.obj["TASKS_FILE"]
    available = runner.list_hooks(tasks_file)

    data = tasks.load_tasks(tasks_file)
    for name in names:
        if name not in available:
            click.echo(f"Error: Hook '{name}' not found.")
            ctx.exit(1)

        if data.config.hooks is None:
            # If currently "all", it's already enabled.
            # We don't transition to an explicit list here to keep the default behavior.
            click.echo(
                f"Hook '{name}' is already active (all available hooks are enabled)."
            )
            continue

        if name not in data.config.hooks:
            data.config.hooks.append(name)
            click.echo(f"Enabled hook: {name}")
        else:
            click.echo(f"Hook '{name}' is already enabled.")

    tasks.save_tasks(tasks_file, data)


@hooks_group.command(name="disable")
@click.argument("names", nargs=-1, required=True)
@click.pass_context
def hooks_disable(ctx: click.Context, names: tuple[str, ...]):
    """Disables one or more orchestrator hooks."""
    tasks_file = ctx.obj["TASKS_FILE"]
    available = runner.list_hooks(tasks_file)

    data = tasks.load_tasks(tasks_file)
    for name in names:
        if name not in available:
            click.echo(f"Error: Hook '{name}' not found.")
            ctx.exit(1)

        if data.config.hooks is None:
            # If currently "all", we transition to an explicit list minus the disabled one
            data.config.hooks = [h for h in available if h != name]
        else:
            data.config.hooks = [h for h in data.config.hooks if h != name]

        click.echo(f"Disabled hook: {name}")

    tasks.save_tasks(tasks_file, data)


@hooks_group.command(name="set")
@click.argument("names", nargs=-1)
@click.pass_context
def hooks_set(ctx: click.Context, names: tuple[str, ...]):
    """Sets the specific list of active orchestrator hooks.

    Provide a space-separated list of hook names. If no names are provided,
    all hooks will be disabled.

    Examples:
      lemming hooks set roadmap lint
      lemming hooks set roadmap
      lemming hooks set  # Disables all hooks
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    available = runner.list_hooks(tasks_file)

    data = tasks.load_tasks(tasks_file)
    new_hooks = []
    for name in names:
        if name not in available:
            click.echo(f"Error: Hook '{name}' not found.")
            ctx.exit(1)
        new_hooks.append(name)

    data.config.hooks = new_hooks
    tasks.save_tasks(tasks_file, data)

    if not new_hooks:
        click.echo("All hooks disabled.")
    else:
        click.echo(f"Active hooks set to: {', '.join(new_hooks)}")


@hooks_group.command(name="reset")
@click.pass_context
def hooks_reset(ctx: click.Context):
    """Resets hooks to run all available hooks by default."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = tasks.load_tasks(tasks_file)
    data.config.hooks = None
    tasks.save_tasks(tasks_file, data)
    click.echo("Hooks reset to default (all available).")


@cli.command(short_help="<taskid> Record a task failure")
@click.argument("task_id")
@click.pass_context
def fail(ctx: click.Context, task_id: str):
    """Records a task failure (requires at least one recorded outcome).

    Args:
        task_id: The ID of the task to mark as failed.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    try:
        target_task = tasks.update_task(
            tasks_file, task_id, status="pending", require_outcomes=True
        )
        click.echo(f"Failure recorded for task {target_task.id}.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@cli.command(short_help="<taskid> Stop an in-progress task")
@click.argument("task_id")
@click.pass_context
def cancel(ctx: click.Context, task_id: str):
    """Kills the runner process for an in-progress task and resets it to pending.

    Args:
        task_id: The ID of the task to cancel.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    if tasks.cancel_task(tasks_file, task_id):
        click.echo(f"Task {task_id} cancelled.")
    else:
        click.echo(f"Error: Task {task_id} not found or not in progress.")
        ctx.exit(1)


@cli.command(short_help="<taskid> Clear a task's attempts and outcomes")
@click.argument("task_id")
@click.pass_context
def reset(ctx: click.Context, task_id: str):
    """Clears all history (attempts, outcomes, and logs) for a specific task.

    Args:
        task_id: The ID of the task to reset.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    try:
        target_task = tasks.reset_task(tasks_file, task_id)
        click.echo(f"Task {target_task.id} attempts, outcomes, and logs cleared.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


def _run_hooks(
    tasks_file: pathlib.Path,
    task_id: str,
    runner_name: str,
    yolo: bool,
    runner_args: tuple,
    no_defaults: bool,
    verbose: bool,
    hooks: list[str] | None = None,
    working_dir: pathlib.Path | None = None,
) -> None:
    """Discovers and executes orchestrator hooks for a finished task.

    Args:
        hooks: Explicit list of hooks to run. If None, uses config.hooks.
    """
    data = tasks.load_tasks(tasks_file)
    task = next((t for t in data.tasks if t.id == task_id), None)
    if not task:
        return

    # Use provided hooks or fall back to configuration
    active_hooks = hooks if hooks is not None else data.config.hooks
    if active_hooks is None:
        active_hooks = runner.list_hooks(tasks_file)

    for hook_name in active_hooks:
        try:
            prompt = runner.prepare_hook_prompt(hook_name, data, task, tasks_file)
        except FileNotFoundError:
            if verbose:
                click.echo(f"Hook '{hook_name}' prompt not found, skipping.")
            continue

        if verbose:
            click.secho(f"\n=== Hook: {hook_name} Prompt ===", fg="magenta", bold=True)
            click.echo(prompt)
            click.secho("========================\n", fg="magenta", bold=True)

        cmd = runner.build_runner_command(
            runner_name,
            prompt,
            yolo,
            runner_args,
            no_defaults,
            verbose=verbose,
        )

        try:
            # Append hooks to the main runner log for a unified execution trace.
            returncode, stdout, stderr = runner.run_with_heartbeat(
                cmd,
                tasks_file,
                task_id,
                verbose,
                echo_fn=lambda line: click.echo(line, nl=False),
                header=f"Hook: {hook_name}",
                cwd=working_dir,
            )
            if verbose:
                if returncode != 0:
                    click.echo(f"Hook '{hook_name}' exited with code {returncode}.")
        except Exception as e:
            click.echo(f"Hook '{hook_name}' error: {e}")


@cli.command(
    short_help="Run the autonomous task execution loop",
)
@click.option(
    "--retry-delay",
    default=10,
    help="Seconds to wait before retrying a failed task (to handle rate limits).",
)
@click.option(
    "--yolo/--no-yolo", default=True, help="Run the runner in YOLO/auto-approve mode."
)
@click.option(
    "--env",
    multiple=True,
    help="Environment variables to set for the runner (e.g. --env KEY=VALUE).",
)
@click.option(
    "--no-defaults",
    is_flag=True,
    help="Do not auto-inject default flags (like --yolo) based on runner name.",
)
@click.argument("runner_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run(
    ctx: click.Context,
    retry_delay: int,
    yolo: bool,
    env: tuple,
    no_defaults: bool,
    runner_args: tuple,
) -> None:
    """Starts the orchestrator loop to autonomously execute pending tasks.

    Args:
        retry_delay: Delay between retries.
        yolo: If True, skip runner confirmations.
        env: Environment variables to inject.
        no_defaults: Skip default flag injection.
        runner_args: Raw arguments passed directly to the runner.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]

    # Determine the project's working directory
    working_dir = paths.get_working_dir(tasks_file)

    # Parse environment overrides
    env_overrides = {}
    for e in env:
        if "=" in e:
            k, v = e.split("=", 1)
            env_overrides[k] = v
        else:
            env_overrides[e] = ""

    if env_overrides:
        os.environ.update(env_overrides)

    runner.ensure_hooks_symlinked()

    tasks.acquire_loop_lock(tasks_file)
    try:
        _run_loop(
            tasks_file,
            verbose,
            retry_delay,
            yolo,
            no_defaults,
            runner_args,
            working_dir=working_dir,
        )
    finally:
        tasks.release_loop_lock(tasks_file)


def _run_loop(
    tasks_file: pathlib.Path,
    verbose: bool,
    retry_delay: int,
    yolo: bool,
    no_defaults: bool,
    runner_args: tuple,
    working_dir: pathlib.Path | None = None,
) -> None:
    while True:
        # Reload configuration on each iteration to respond to changes (e.g., from Web UI)
        data = tasks.load_tasks(tasks_file)
        retries = data.config.retries
        runner_name = data.config.runner
        active_hooks = data.config.hooks
        if active_hooks is None:
            active_hooks = runner.list_hooks(tasks_file)

        current_task = tasks.get_pending_task(data)

        if not current_task:
            click.echo("All tasks completed!")
            break

        task_id = current_task.id

        if current_task.attempts >= retries:
            # Run hooks (like roadmap revision) even on final failure to give them
            # a chance to heal the task before we abort.
            _run_hooks(
                tasks_file,
                task_id,
                runner_name,
                yolo,
                runner_args,
                no_defaults,
                verbose,
                hooks=active_hooks,
                working_dir=working_dir,
            )

            # Re-check: if a hook reset/edited/replaced the task, continue the loop
            data = tasks.load_tasks(tasks_file)
            healed_task = next((t for t in data.tasks if t.id == task_id), None)
            if healed_task and healed_task.attempts >= retries:
                click.echo(
                    f"\nTask {task_id} failed after {retries} attempts. Aborting run."
                )
                break

            # Orchestrator healed it (reset attempts, deleted it, etc.) — continue the loop
            click.echo(f"Orchestrator intervened on task {task_id}. Continuing...")
            continue

        # Add a small random jitter to avoid race conditions between multiple instances
        time.sleep(random.uniform(0.1, 0.5))

        # Try to claim the task
        current_task = tasks.claim_task(tasks_file, task_id, pid=os.getpid())
        if not current_task:
            if verbose:
                click.echo(
                    f"Task {task_id} already claimed by another instance. Skipping."
                )
            continue

        if verbose:
            click.echo(
                f"\n--- Task {task_id} (Attempt {current_task.attempts}/{retries}) ---"
            )
            click.echo(f"Working on: {current_task.description}")
        else:
            click.echo(
                f"[{task_id}] Attempt {current_task.attempts}/{retries}: {current_task.description}"
            )

        prompt = runner.prepare_prompt(data, current_task, tasks_file)

        if verbose:
            click.secho("\n=== Runner Prompt ===", fg="blue", bold=True)
            click.echo(prompt)
            click.secho("====================\n", fg="blue", bold=True)

        cmd = runner.build_runner_command(
            current_task.runner or runner_name,
            prompt,
            yolo,
            runner_args,
            no_defaults,
            verbose=verbose,
        )

        returncode = 0
        stdout, stderr = "", ""
        try:
            returncode, stdout, stderr = runner.run_with_heartbeat(
                cmd,
                tasks_file,
                task_id,
                verbose,
                echo_fn=lambda line: click.echo(line, nl=False),
                cwd=working_dir,
            )
            if returncode != 0:
                if not verbose:
                    if stdout:
                        click.echo(stdout, err=True)
                    if stderr:
                        click.echo(stderr, err=True)
                click.echo(
                    f"\n{runner_name.capitalize()} execution failed with exit code {returncode}"
                )
                if returncode == 127:
                    click.echo(
                        f"\nNOTE: Command '{runner_name}' not found.\n"
                        "If you are using a shell alias, Python subprocesses cannot see it.\n"
                        "Fixes:\n"
                        f"1. Use the absolute path: `lemming config set runner /path/to/{runner_name}`\n"
                        f"2. Create an executable wrapper script for '{runner_name}' in your PATH."
                    )
        except Exception as e:
            click.echo(f"\nAn error occurred while executing {runner_name}: {e}")

        # Run orchestrator hooks while the task is still claimed and "in_progress".
        # This keeps the execution sequence unified in the logs and ensures
        # that another loop doesn't pick up the next task prematurely.
        _run_hooks(
            tasks_file,
            task_id,
            runner_name,
            yolo,
            runner_args,
            no_defaults,
            verbose,
            hooks=active_hooks,
            working_dir=working_dir,
        )

        # Post-execution validation and heartbeat cleanup
        post_task = tasks.finish_task_attempt(tasks_file, task_id)

        if not post_task:
            click.echo("Error: Task disappeared from roadmap during execution.")
            break

        if post_task.status == "completed":
            if verbose:
                click.echo("Runner successfully reported task completion.")
            else:
                click.echo(f"[{task_id}] Task completed successfully!")
        else:
            if not verbose:
                if stdout:
                    click.echo(stdout)
                if stderr:
                    click.echo(stderr, err=True)
            if verbose:
                click.echo(
                    "Runner finished execution but did NOT report completion. Retrying..."
                )
            if post_task.attempts < retries and retry_delay > 0:
                if verbose:
                    click.echo(
                        f"Waiting {retry_delay} seconds before next attempt to avoid rate limits..."
                    )
                time.sleep(retry_delay)


def parse_timeout(t_str: str) -> float:
    """Parses a duration string into seconds.

    Args:
        t_str: Duration string (e.g., '8h', '30m', '90s').

    Returns:
        The duration in seconds as a float.
    """
    t_str = t_str.strip()
    if t_str == "0" or t_str.startswith("-"):
        return 0.0

    multiplier = 1.0
    if t_str.endswith("h"):
        multiplier = 3600.0
        t_str = t_str[:-1]
    elif t_str.endswith("m"):
        multiplier = 60.0
        t_str = t_str[:-1]
    elif t_str.endswith("s"):
        t_str = t_str[:-1]

    try:
        return float(t_str) * multiplier
    except ValueError:
        return 0.0


@cli.command(short_help="Launch the web interface")
@click.option("--port", default=8999, help="Port to run the server on.")
@click.option("--host", default="127.0.0.1", help="Host to bind the server to.")
@click.option(
    "--tunnel",
    default=None,
    type=click.Choice(["cloudflare", "tailscale"]),
    help="Expose via a public tunnel (cloudflare or tailscale).",
)
@click.option(
    "--timeout",
    default=None,
    help="Auto-shutdown after duration (e.g., '8h', '30m', '0' to disable). Defaults to '8h' when --tunnel is used.",
)
@click.pass_context
def serve(
    ctx: click.Context, port: int, host: str, tunnel: str | None, timeout: str | None
):
    """Launches the local web dashboard for monitoring and interaction.

    Optionally exposes it to the public internet via --tunnel.
    """
    import copy
    import os
    import secrets
    import sys
    import threading

    import uvicorn
    import uvicorn.config

    from . import api
    from . import providers

    runner.ensure_hooks_symlinked()

    api.app.state.tasks_file = ctx.obj["TASKS_FILE"]
    api.app.state.verbose = ctx.obj["VERBOSE"]
    api.app.state.root = pathlib.Path.cwd().resolve()

    tunnel_proc = None
    if tunnel:
        click.echo(f"[ Lemming ] Starting local server on port {port}...")
        click.echo(f"[ Lemming ] Initiating public tunnel via {tunnel.capitalize()}...")

        tunnel_proc = (
            providers.CloudflareProvider()
            if tunnel == "cloudflare"
            else providers.TailscaleProvider()
        )
        try:
            public_url = tunnel_proc.start(port)
        except Exception as e:
            click.echo(f"[ Lemming ] Error starting tunnel: {e}", err=True)
            sys.exit(1)

        token = secrets.token_urlsafe(32)
        api.app.state.share_token = token

        click.echo("[ Lemming ] ")
        click.echo("[ Lemming ] ⚠️  SECURITY WARNING ")
        click.echo(
            "[ Lemming ] Your Lemming instance is being exposed to the public internet."
        )
        click.echo(
            "[ Lemming ] Token-based authentication has been automatically enabled."
        )
        click.echo("[ Lemming ] ")
        click.echo("[ Lemming ] 🌐 Share this exact, secure link with the remote user:")
        click.echo(f"[ Lemming ] 👉 {public_url}?token={token}")
        click.echo("")
    else:
        click.echo(f"Launching Lemming UI at http://{host}:{port}")

    # Default timeout to 8h for tunnel mode, 0 (disabled) for local mode.
    timeout_str = timeout if timeout is not None else ("8h" if tunnel else "0")
    timeout_seconds = parse_timeout(timeout_str)

    if timeout_seconds > 0:
        click.echo(
            f"[ Lemming ] The server will automatically shut down in {timeout_str}."
        )

        def monitor():
            time.sleep(timeout_seconds)
            click.echo("\n[ Lemming ] Timeout reached. Waiting for tasks to finish...")
            if tunnel_proc:
                tunnel_proc.stop()

            tasks_file = api.app.state.tasks_file
            while True:
                project_data = tasks.get_project_data(tasks_file)
                if not project_data.loop_running:
                    break
                time.sleep(5)

            click.echo("[ Lemming ] All tasks finished. Exiting.")
            os._exit(0)

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()

    if tunnel:
        click.echo(
            "[ Lemming ] Press Ctrl+C to manually close the tunnel and shut down the server."
        )

    # Suppress repetitive access-log lines from UI polling endpoints.
    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["filters"] = {
        "quiet_poll": {"()": "lemming.api.QuietPollFilter"},
    }
    log_config["handlers"]["access"]["filters"] = ["quiet_poll"]

    try:
        uvicorn.run(api.app, host=host, port=port, log_config=log_config)
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel_proc:
            tunnel_proc.stop()


if __name__ == "__main__":
    cli()
