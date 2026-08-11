"""CLI command for the long-form task brief."""

import typing

import click

from .. import paths, tasks
from .main import cli


@cli.command(short_help="<taskid> [text] View or set a task's long-form brief")
@click.argument("task_id")
@click.argument("text", required=False)
@click.option(
    "--file",
    "-f",
    type=click.File("r"),
    help="Read the brief from a file (or - for stdin).",
)
@click.pass_context
def brief(
    ctx: click.Context,
    task_id: str,
    text: str | None,
    file: typing.TextIO | None,
):
    """Views or sets the long-form brief delivered with a task.

    The brief holds evidence that does not belong in the description: measured
    timings, exact failing selectors, why a previous attempt was wrong. It has
    no length cap and is appended to the runner prompt automatically, so the
    description never has to point at it.

    Examples:
      lemming brief a1b2c3d4
      lemming brief a1b2c3d4 "Timings: first paint 2.4s"
      lemming brief a1b2c3d4 --file notes.md
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    data = tasks.load_tasks(tasks_file)
    try:
        target = tasks.resolve_task(data.tasks, task_id)
    except (tasks.TaskNotFoundError, tasks.AmbiguousTaskIdError) as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)

    if file and text:
        click.echo("Error: Cannot provide both brief text and --file.")
        ctx.exit(1)

    brief_file = paths.get_brief_file(tasks_file, target.id)

    # With no new content, the command reads the brief back.
    if not file and not text:
        if not brief_file.exists() or not brief_file.read_text().strip():
            click.echo(f"No brief for task {target.id}.")
            return
        click.echo(brief_file.read_text(encoding="utf-8"))
        return

    content = (file.read() if file else text or "").strip()
    if not content:
        click.echo("Error: Must provide either brief text or --file.")
        ctx.exit(1)

    brief_file.write_text(content + "\n", encoding="utf-8")
    click.echo(
        f"Brief for task {target.id} saved ({len(content):,} characters)."
    )
