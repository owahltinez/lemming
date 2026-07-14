"""CLI command for viewing or setting the project's long-term goal."""

import pathlib

import click

from .. import tasks
from .main import cli


@cli.command(short_help="[<text>] View or set the long-term goal")
@click.argument("goal_text", required=False)
@click.option(
    "--file",
    "-f",
    type=click.Path(exists=True, path_type=pathlib.Path),
    help="Read the goal from a file.",
)
@click.pass_context
def goal(ctx: click.Context, goal_text: str | None, file: pathlib.Path | None):
    """Sets or displays the long-term goal shared across all tasks.

    Args:
        ctx: The click context holding shared CLI state.
        goal_text: The goal string to set (optional).
        file: A file path to read the goal from (optional).
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    if file:
        tasks.update_goal(tasks_file, file.read_text(encoding="utf-8"))
        click.echo("Long-term goal updated.")
    elif goal_text:
        tasks.update_goal(tasks_file, goal_text)
        click.echo("Long-term goal updated.")
    else:
        data = tasks.load_tasks(tasks_file)
        click.echo(data.goal or "No goal set.")
