import typing

import click

from .. import tasks
from .main import cli


@cli.command(short_help="<taskid> [text] Add a progress entry")
@click.argument("task_id")
@click.argument("text", required=False)
@click.option(
    "--file",
    "-f",
    type=click.File("r"),
    help="Read progress text from a file (or - for stdin).",
)
@click.pass_context
def progress(
    ctx: click.Context,
    task_id: str,
    text: typing.Optional[str],
    file: typing.Optional[typing.TextIO],
):
    """Records a progress entry or finding for a specific task.

    Args:
        task_id: The ID of the task.
        text: The progress to record (optional if --file is used).
        file: An optional file to read the progress from.
    """
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
        target_task = tasks.add_progress(tasks_file, task_id, text)
        click.echo(f"Progress added to task {target_task.id}.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)
