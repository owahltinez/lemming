"""CLI command for viewing or setting the project's long-term goal."""

import typing

import click

from .. import persistence
from ..tasks import operations
from .main import TEXT_FILE, cli


@cli.command(short_help="[<text>] View or set the long-term goal")
@click.argument("goal_text", required=False)
@click.option(
    "--file",
    "-f",
    type=TEXT_FILE,
    help="Read the goal from a file (or - for stdin).",
)
@click.pass_context
def goal(ctx: click.Context, goal_text: str | None, file: typing.TextIO | None):
    """Sets or displays the long-term goal shared across all tasks."""
    tasks_file = ctx.obj["TASKS_FILE"]

    if file:
        operations.update_goal(tasks_file, file.read())
        click.echo("Long-term goal updated.")
    elif goal_text:
        operations.update_goal(tasks_file, goal_text)
        click.echo("Long-term goal updated.")
    else:
        data = persistence.load_tasks(tasks_file)
        click.echo(data.goal or "No goal set.")
