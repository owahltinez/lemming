"""CLI command for recording task progress entries."""

import time
import typing

import click

from .. import models, orchestrator, persistence
from ..tasks import progress as task_progress
from .main import TEXT_FILE, cli


def _format_budget_suffix(task: models.Task, time_limit: int) -> str:
    """Formats elapsed and remaining time for an active bounded execution."""
    component = task.active_execution_component
    started_at = task.active_execution_started_at
    if time_limit <= 0 or not component or started_at is None:
        return ""
    if component == "runner":
        limit_min = time_limit
    elif component.startswith("hook:"):
        limit_min = min(time_limit, orchestrator.HOOK_TIME_LIMIT)
    else:
        return ""

    elapsed_s = max(0, int(time.time() - started_at))
    elapsed = "<1m" if elapsed_s < 60 else f"{elapsed_s // 60}m"
    remaining_s = limit_min * 60 - elapsed_s
    if remaining_s <= 0:
        return f" ({elapsed}/{limit_min}m elapsed, time limit reached)"
    remaining = "<1m" if remaining_s < 60 else f"{remaining_s // 60}m"
    return f" ({elapsed}/{limit_min}m elapsed, {remaining} remaining)"


@cli.command(short_help="<taskid> [text] Add a progress entry")
@click.argument("task_id")
@click.argument("text", required=False)
@click.option(
    "--file",
    "-f",
    type=TEXT_FILE,
    help="Read progress text from a file (or - for stdin).",
)
@click.pass_context
def progress(
    ctx: click.Context,
    task_id: str,
    text: str | None,
    file: typing.TextIO | None,
):
    """Records a progress entry or finding for a specific task."""
    tasks_file = ctx.obj["TASKS_FILE"]

    if file:
        if text:
            click.echo("Error: Cannot provide both progress text and --file.")
            ctx.exit(1)
        text = file.read().strip()
    elif text:
        text = text.strip()

    if not text:
        click.echo("Error: Must provide either progress text or --file.")
        ctx.exit(1)

    try:
        target_task = task_progress.add_progress(tasks_file, task_id, text)
        time_limit = persistence.load_tasks(tasks_file).config.time_limit
        suffix = _format_budget_suffix(target_task, time_limit)
        click.echo(f"Progress added to task {target_task.id}{suffix}.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)
