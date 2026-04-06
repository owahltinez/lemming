import typing
import click
from .main import cli
from .. import tasks


class ProgressGroup(click.Group):
    """Custom group to handle `lemming progress <id> <text>` syntax."""

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


@cli.group(cls=ProgressGroup, short_help="Manage task progress")
def progress():
    """Manages progress entries and findings for specific tasks."""
    pass


@progress.command(name="add", short_help="<taskid> [text] Add a progress entry")
@click.argument("task_id")
@click.argument("text", required=False)
@click.option(
    "--file",
    "-f",
    type=click.File("r"),
    help="Read progress text from a file (or - for stdin).",
)
@click.pass_context
def progress_add(
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


@progress.command(name="delete", short_help="<taskid> <index> Delete a progress entry")
@click.argument("task_id")
@click.argument("index", type=int)
@click.pass_context
def progress_delete(ctx: click.Context, task_id: str, index: int):
    """Deletes a progress entry from a task by its index (starting from 0).

    Args:
        task_id: The ID of the task.
        index: The index of the progress entry to delete.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    try:
        target_task = tasks.delete_progress(tasks_file, task_id, index)
        click.echo(f"Progress {index} deleted from task {target_task.id}.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@progress.command(
    name="edit", short_help="<taskid> <index> [new_text] Edit a progress entry"
)
@click.argument("task_id")
@click.argument("index", type=int)
@click.argument("text", required=False)
@click.option(
    "--file",
    "-f",
    type=click.File("r"),
    help="Read progress text from a file (or - for stdin).",
)
@click.pass_context
def progress_edit(
    ctx: click.Context,
    task_id: str,
    index: int,
    text: typing.Optional[str],
    file: typing.Optional[typing.TextIO],
):
    """Edits an existing progress entry for a task by its index (starting from 0).

    Args:
        task_id: The ID of the task.
        index: The index of the progress entry to edit.
        text: The new progress text (optional if --file is used).
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
        target_task = tasks.edit_progress(tasks_file, task_id, index, text)
        click.echo(f"Progress {index} updated for task {target_task.id}.")
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)


@progress.command(name="list", short_help="<taskid> List progress for a task")
@click.argument("task_id")
@click.pass_context
def progress_list(ctx: click.Context, task_id: str):
    """Lists all progress entries for a specific task with their indices.

    Args:
        task_id: The ID of the task.
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    data = tasks.load_tasks(tasks_file)
    target = next((t for t in data.tasks if t.id.startswith(task_id)), None)

    if not target:
        click.echo(f"Error: Task {task_id} not found.")
        ctx.exit(1)

    if not target.progress:
        click.echo(f"No progress for task {target.id}.")
        return

    click.secho(f"Progress for task {target.id}:", bold=True)
    for i, o in enumerate(target.progress):
        click.echo(f"[{i}] {o}")
