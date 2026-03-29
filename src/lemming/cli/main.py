import pathlib
import click
from .. import paths


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
