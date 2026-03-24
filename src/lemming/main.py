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
@click.pass_context
def add(
    ctx: click.Context,
    description: str,
    index: int,
    runner_name: str | None,
    parent: str | None,
):
    """Adds a new task to the roadmap queue.

    Args:
        description: A text description of the task to perform.
        index: The position in the roadmap to insert the task.
        runner_name: An optional custom runner to use for this specific task.
        parent: Optional parent task ID.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]

    new_task = tasks.add_task(tasks_file, description, runner_name, index, parent)
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
@click.pass_context
def edit(
    ctx: click.Context,
    task_id: str,
    description: str | None,
    runner_name: str | None,
    index: int | None,
    parent: str | None,
):
    """Edits an existing task's description, preferred runner, position, or parent.

    Args:
        task_id: The ID of the task to update.
        description: The new description (optional).
        runner_name: The new preferred runner (optional).
        index: The new position in the roadmap (optional).
        parent: The new parent task ID (optional).
    """
    if description is None and runner_name is None and index is None and parent is None:
        click.echo(
            "Error: At least one of --description, --runner, --index, or --parent must be provided."
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
    if paths.get_log_file(tasks_file, target.id, "runner").exists():
        log_parts.append("runner")
    if paths.get_log_file(tasks_file, target.id, "review").exists():
        log_parts.append("review")
    click.echo(f"Logs:        {', '.join(log_parts) if log_parts else 'None'}")

    if target.completed_at:
        comp_time = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(target.completed_at)
        )
        click.echo(f"Completed At: {comp_time}")
    run_time = target.run_time
    if target.status == "in_progress" and target.started_at:
        run_time += time.time() - target.started_at

    if run_time > 0:
        if run_time < 60:
            rt_str = f"{run_time:.1f}s"
        else:
            rt_str = f"{int(run_time // 60)}m {int(run_time % 60)}s"
        click.echo(f"Run Time:     {rt_str}")

    if target.outcomes:
        click.secho("\n--- Outcomes ---", fg="magenta", bold=True)
        for outcome in target.outcomes:
            click.echo(f"- {outcome}")


@cli.command(short_help="<taskid> Print a task's log to stdout")
@click.argument("task_id")
@click.option(
    "--name",
    default="runner",
    type=click.Choice(["runner", "review"]),
    help="Which log to display (default: runner).",
)
@click.pass_context
def logs(ctx: click.Context, task_id: str, name: str):
    """Prints the log for a task to stdout.

    Args:
        task_id: The ID (or prefix) of the task.
        name: Which log to display ("runner" or "review").
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    data = tasks.load_tasks(tasks_file)
    target = next((t for t in data.tasks if t.id.startswith(task_id)), None)
    if not target:
        click.echo(f"Error: Task {task_id} not found.")
        ctx.exit(1)

    log_file = paths.get_log_file(tasks_file, target.id, name)
    if not log_file.exists():
        click.echo(f"No {name} log for task {target.id}.")
        ctx.exit(1)

    click.echo(log_file.read_text(encoding="utf-8"), nl=False)


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


@cli.command(short_help="<taskid> <text> Add an outcome to a task")
@click.argument("task_id")
@click.argument("text")
@click.pass_context
def outcome(ctx: click.Context, task_id: str, text: str):
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


def _run_reviewer(
    tasks_file: pathlib.Path,
    finished_task_id: str,
    runner_name: str,
    yolo: bool,
    runner_args: tuple,
    no_defaults: bool,
    verbose: bool,
) -> None:
    """Runs the reviewer to evaluate and adapt the roadmap.

    Args:
        tasks_file: Path to the tasks YAML file.
        finished_task_id: ID of the task that just finished.
        runner_name: The CLI runner to invoke.
        yolo: If True, skip runner confirmations.
        runner_args: Raw arguments passed directly to the runner.
        no_defaults: Skip default flag injection.
        verbose: If True, echo reviewer output.
    """
    data = tasks.load_tasks(tasks_file)
    finished_task = next((t for t in data.tasks if t.id == finished_task_id), None)
    if not finished_task:
        return

    if verbose:
        click.echo("\n--- Running roadmap review ---")

    prompt = runner.prepare_review_prompt(data, finished_task, tasks_file)

    if verbose:
        click.secho("\n=== Reviewer Prompt ===", fg="magenta", bold=True)
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
        returncode, stdout, stderr = runner.run_with_heartbeat(
            cmd,
            tasks_file,
            finished_task_id,
            verbose,
            echo_fn=lambda line: click.echo(line, nl=False),
            log_name="review",
        )
        if verbose:
            if returncode == 0:
                click.echo("Reviewer finished.")
            else:
                click.echo(f"Reviewer exited with code {returncode}.")
    except Exception as e:
        click.echo(f"Reviewer error: {e}")


@cli.command(
    short_help="Run the autonomous task execution loop",
)
@click.option(
    "--max-attempts", default=3, help="Maximum number of retries for a single task."
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
    "--runner",
    "runner_name",
    default="gemini",
    help="The underlying CLI runner to use (gemini, aider, claude, codex).",
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
@click.option(
    "--review/--no-review",
    default=False,
    help="Run a reviewer after each task to adapt the roadmap.",
)
@click.option(
    "--review-runner",
    "review_runner",
    default=None,
    help="Runner to use for reviews (defaults to --runner).",
)
@click.argument("runner_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run(
    ctx: click.Context,
    max_attempts: int,
    retry_delay: int,
    yolo: bool,
    runner_name: str,
    env: tuple,
    no_defaults: bool,
    review: bool,
    review_runner: str | None,
    runner_args: tuple,
) -> None:
    """Starts the orchestrator loop to autonomously execute pending tasks.

    Args:
        max_attempts: Maximum retries per task.
        retry_delay: Delay between retries.
        yolo: If True, skip runner confirmations.
        runner_name: The CLI runner to invoke.
        env: Environment variables to inject.
        no_defaults: Skip default flag injection.
        review_runner: Runner to use for reviews (falls back to runner_name).
        runner_args: Raw arguments passed directly to the runner.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]

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

    tasks.acquire_loop_lock(tasks_file)
    try:
        _run_loop(
            tasks_file,
            verbose,
            max_attempts,
            retry_delay,
            yolo,
            runner_name,
            no_defaults,
            review,
            review_runner,
            runner_args,
        )
    finally:
        tasks.release_loop_lock(tasks_file)


def _run_loop(
    tasks_file: pathlib.Path,
    verbose: bool,
    max_attempts: int,
    retry_delay: int,
    yolo: bool,
    runner_name: str,
    no_defaults: bool,
    review: bool,
    review_runner: str | None,
    runner_args: tuple,
) -> None:
    while True:
        data = tasks.load_tasks(tasks_file)
        current_task = tasks.get_pending_task(data)

        if not current_task:
            click.echo("All tasks completed!")
            break

        task_id = current_task.id

        if current_task.attempts >= max_attempts:
            if review:
                # Give the reviewer a chance to heal the task before aborting
                click.echo(
                    f"\nTask {task_id} reached {max_attempts} attempts. Running reviewer..."
                )
                _run_reviewer(
                    tasks_file,
                    task_id,
                    review_runner or runner_name,
                    yolo,
                    runner_args,
                    no_defaults,
                    verbose,
                )
                # Re-check: if the reviewer reset/edited/replaced the task, continue
                data = tasks.load_tasks(tasks_file)
                healed_task = next((t for t in data.tasks if t.id == task_id), None)
                if healed_task and healed_task.attempts >= max_attempts:
                    click.echo(
                        f"Task {task_id} still at max attempts after review. Aborting run."
                    )
                    break
                # Reviewer healed it (reset attempts, deleted it, etc.) — continue the loop
                click.echo(f"Reviewer intervened on task {task_id}. Continuing...")
                continue
            else:
                click.echo(
                    f"\nTask {task_id} failed after {max_attempts} attempts. Aborting run."
                )
                break

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
                f"\n--- Task {task_id} (Attempt {current_task.attempts}/{max_attempts}) ---"
            )
            click.echo(f"Working on: {current_task.description}")
        else:
            click.echo(
                f"[{task_id}] Attempt {current_task.attempts}/{max_attempts}: {current_task.description}"
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
                        f"1. Use the absolute path: `lemming run --runner /path/to/{runner_name}`\n"
                        f"2. Create an executable wrapper script for '{runner_name}' in your PATH."
                    )
        except Exception as e:
            click.echo(f"\nAn error occurred while executing {runner_name}: {e}")

        # Post-execution validation
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
            if post_task.attempts < max_attempts and retry_delay > 0:
                if verbose:
                    click.echo(
                        f"Waiting {retry_delay} seconds before next attempt to avoid rate limits..."
                    )
                time.sleep(retry_delay)

        # Run the reviewer after each task execution
        if review:
            _run_reviewer(
                tasks_file,
                task_id,
                review_runner or runner_name,
                yolo,
                runner_args,
                no_defaults,
                verbose,
            )


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

    api.app.state.tasks_file = ctx.obj["TASKS_FILE"]
    api.app.state.verbose = ctx.obj["VERBOSE"]

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
