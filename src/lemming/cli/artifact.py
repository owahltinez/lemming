"""CLI command for a task's out-of-band diagnostic artifacts."""

import typing

import click

from .. import models, paths, persistence
from ..tasks import limits, progress, queries
from .main import cli


def _is_flat_name(name: str) -> bool:
    """Checks that an artifact name is a plain filename, not a path."""
    return (
        bool(name)
        and name not in (".", "..")
        and ".." not in name
        and not any(separator in name for separator in ("/", "\\"))
    )


@cli.command(
    short_help="<taskid> [name] [text] Store or read verbose diagnostics"
)
@click.argument("task_id")
@click.argument("name", required=False)
@click.argument("text", required=False)
@click.option(
    "--file",
    "-f",
    # Artifacts exist to capture exactly the output that is not clean UTF-8,
    # so undecodable bytes are replaced rather than raised on.
    type=click.File("r", errors="replace"),
    help="Read the artifact from a file (or - for stdin).",
)
@click.option(
    "--note",
    help="Also record a one-line progress entry pointing at the artifact.",
)
@click.pass_context
def artifact(
    ctx: click.Context,
    task_id: str,
    name: str | None,
    text: str | None,
    file: typing.TextIO | None,
    note: str | None,
):
    """Stores or reads verbose diagnostics outside of the tasks file.

    Stack traces, test dumps, and long logs pasted into progress bloat the
    prompt of every later attempt. Keep them here instead: artifacts are
    stored per task outside the workspace and later attempts see their
    names, so the evidence survives even if the pointer is lost.

    Examples:
      lemming artifact a1b2c3d4
      lemming artifact a1b2c3d4 pytest.log
      lemming artifact a1b2c3d4 pytest.log --file - --note 'suite fails'
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    data = persistence.load_tasks(tasks_file)
    try:
        target = queries.resolve_task(data.tasks, task_id)
    except (models.TaskNotFoundError, models.AmbiguousTaskIdError) as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)

    artifacts_dir = paths.get_artifacts_dir(tasks_file, target.id)

    # Content with nowhere to go would otherwise be read and silently dropped.
    if not name and (file or note):
        click.echo("Error: An artifact name is required with --file/--note.")
        ctx.exit(1)

    # With no name, the command lists what the task has stored so far.
    if not name:
        existing = paths.list_artifacts(tasks_file, target.id)
        if not existing:
            click.echo(f"No artifacts for task {target.id}.")
            return
        click.echo(f"Artifacts for task {target.id} in {artifacts_dir}:")
        for entry in existing:
            click.echo(f"  {entry}")
        return

    if not _is_flat_name(name):
        click.echo(
            "Error: Artifact names must be plain filenames, without "
            "directory separators or '..'."
        )
        ctx.exit(1)

    if file and text:
        click.echo("Error: Cannot provide both artifact text and --file.")
        ctx.exit(1)

    artifact_file = artifacts_dir / name
    if artifact_file.is_dir():
        click.echo(f"Error: {artifact_file} is a directory, not an artifact.")
        ctx.exit(1)

    # With no new content, the command reads the artifact back.
    if not file and not text:
        if note:
            click.echo("Error: --note requires artifact text or --file.")
            ctx.exit(1)
        if not artifact_file.exists():
            click.echo(f"No artifact {name} for task {target.id}.")
            ctx.exit(1)
        click.echo(artifact_file.read_text(encoding="utf-8", errors="replace"))
        return

    content = (file.read() if file else text or "").strip()
    if not content:
        click.echo("Error: Must provide either artifact text or --file.")
        ctx.exit(1)

    # An over-long pointer is caught before the write so the common mistake
    # does not leave a stale artifact behind.
    pointer = f"{note.strip()} (see {artifact_file})" if note else ""
    if pointer and len(pointer) > limits.MAX_PROGRESS_ENTRY_CHARS:
        click.echo(
            f"Error: Progress note would be {len(pointer):,} characters "
            f"(limit {limits.MAX_PROGRESS_ENTRY_CHARS:,}). Shorten --note; "
            "the artifact itself has no cap."
        )
        ctx.exit(1)

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text(content + "\n", encoding="utf-8")
    click.echo(str(artifact_file))

    if pointer:
        try:
            progress.add_progress(tasks_file, target.id, pointer)
        except ValueError as e:
            click.echo(f"Error: {e}")
            ctx.exit(1)
        click.echo(f"Progress added to task {target.id}.")
