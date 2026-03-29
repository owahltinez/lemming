import typing
import click
from .main import cli
from .. import tasks


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


@outcome.command(name="add", short_help="<taskid> [text] Add an outcome")
@click.argument("task_id")
@click.argument("text", required=False)
@click.option(
    "--file",
    "-f",
    type=click.File("r"),
    help="Read outcome text from a file (or - for stdin).",
)
@click.pass_context
def outcome_add(
    ctx: click.Context,
    task_id: str,
    text: typing.Optional[str],
    file: typing.Optional[typing.TextIO],
):
    """Records a technical outcome or finding for a specific task.

    Args:
        task_id: The ID of the task.
        text: The technical detail or outcome to record (optional if --file is used).
        file: An optional file to read the outcome from.
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    if file:
        if text:
            click.echo("Error: Cannot provide both outcome text and --file.")
            ctx.exit(1)
        text = file.read().strip()
    elif text:
        text = text.strip()

    if not text:
        click.echo("Error: Must provide either outcome text or --file.")
        ctx.exit(1)

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


@outcome.command(name="edit", short_help="<taskid> <index> [new_text] Edit an outcome")
@click.argument("task_id")
@click.argument("index", type=int)
@click.argument("text", required=False)
@click.option(
    "--file",
    "-f",
    type=click.File("r"),
    help="Read outcome text from a file (or - for stdin).",
)
@click.pass_context
def outcome_edit(
    ctx: click.Context,
    task_id: str,
    index: int,
    text: typing.Optional[str],
    file: typing.Optional[typing.TextIO],
):
    """Edits an existing outcome for a task by its index (starting from 0).

    Args:
        task_id: The ID of the task.
        index: The index of the outcome to edit.
        text: The new outcome text (optional if --file is used).
        file: An optional file to read the outcome from.
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    if file:
        if text:
            click.echo("Error: Cannot provide both outcome text and --file.")
            ctx.exit(1)
        text = file.read().strip()
    elif text:
        text = text.strip()

    if not text:
        click.echo("Error: Must provide either outcome text or --file.")
        ctx.exit(1)

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
