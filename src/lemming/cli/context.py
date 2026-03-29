import pathlib
import click
from .main import cli
from .. import tasks


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
