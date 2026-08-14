"""CLI command for a single one-shot task, run outside any roadmap.

Where ``run`` drives a persistent roadmap, ``exec`` normalizes the agent
CLIs behind one interface for a single unit of work: the caller names a
task and a runner, and gets the agent's closing message back on stdout.

The run is hermetic. Nothing is read from the project's own roadmap, whose
goal and progress may describe work abandoned long ago, and the state it
does keep lives in a directory removed when the run succeeds.
"""

import contextlib
import pathlib
import shutil
import sys
import typing

import click

from .. import paths, runner, scope, shutdown, tasks
from ..hooks import FAILURE_HOOK_PRIORITY, get_hook_priority, list_hooks
from ..orchestrator import run_loop
from .main import cli

# Requests every review rather than making the caller name each one.
ALL_REVIEWS = "all"

# Ceiling on how much of the runner log is scanned for the closing message.
# The message is at the end, and a log carrying every tool call can be
# large enough that reading it whole is wasteful.
MAX_LOG_TAIL_BYTES = 256 * 1024


def _read_log_tail(
    log_file: pathlib.Path, max_bytes: int = MAX_LOG_TAIL_BYTES
) -> str:
    """Returns the end of a runner log, or an empty string if unreadable.

    Args:
        log_file: Path to the runner log.
        max_bytes: Most bytes to read from the end of the file.

    Returns:
        The tail of the log, decoded leniently. A leading partial line is
        harmless: the extractor skips anything that is not a whole event.
    """
    try:
        with open(log_file, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - max_bytes))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _resolve_prompt(description: str | None, file: typing.TextIO | None) -> str:
    """Returns the prompt text from an argument or a file.

    Args:
        description: Prompt given as a command-line argument.
        file: Open file (or stdin) to read the prompt from.

    Returns:
        The prompt text, stripped.

    Raises:
        click.UsageError: If neither or both sources were given.
    """
    if description and file:
        raise click.UsageError("Cannot provide both a description and --file.")
    text = (file.read() if file else description or "").strip()
    if not text:
        raise click.UsageError("Provide a task description or --file.")
    return text


def _create_review_task(
    exec_dir: pathlib.Path, reviews: list[str], config
) -> str:
    """Scaffolds a task that is already finished, so only reviews run.

    A review inspects work that already exists, leaving a task runner with
    nothing to do. The orchestrator already skips the runner for a task
    caught mid-finalization, which is exactly this situation.

    Args:
        exec_dir: Directory holding this run's ephemeral state.
        reviews: Names of the reviews about to run.
        config: Roadmap configuration for the run.

    Returns:
        The new task's ID.
    """
    tasks_file = exec_dir / "tasks.yml"
    task = tasks.Task(
        id=tasks.generate_task_id(),
        description=f"Review the workspace: {', '.join(reviews)}.",
        status=tasks.TaskStatus.IN_PROGRESS,
        requested_status=tasks.TaskStatus.COMPLETED,
    )
    tasks.save_tasks(tasks_file, tasks.Roadmap(config=config, tasks=[task]))
    return task.id


def _create_task(exec_dir: pathlib.Path, prompt: str, config) -> str:
    """Scaffolds the ephemeral roadmap holding this run's single task.

    A delegating agent's prompt routinely exceeds the roadmap description
    limit, which exists to protect context shared across many tasks. There
    is no such budget here, so the overflow goes to the brief, which has no
    cap and reaches the runner just the same.

    Args:
        exec_dir: Directory holding this run's ephemeral state.
        prompt: The full prompt text.
        config: Roadmap configuration for the run.

    Returns:
        The new task's ID.
    """
    tasks_file = exec_dir / "tasks.yml"
    tasks.save_tasks(tasks_file, tasks.Roadmap(config=config))

    limit = tasks.MAX_TASK_DESCRIPTION_CHARS
    fits = len(prompt) <= limit
    description = prompt if fits else f"{prompt[: limit - 1]}…"
    task = tasks.add_task(tasks_file, description)
    if not fits:
        paths.get_brief_file(tasks_file, task.id).write_text(
            prompt, encoding="utf-8"
        )
    return task.id


def _resolve_reviews(values: tuple[str, ...]) -> list[str]:
    """Returns the reviews to run, in execution order.

    Hooks are discovered from the built-in and global layers only. A
    project's own hooks may be as stale as its roadmap, and the caller may
    be standing in a repository they merely checked out.

    Args:
        values: Requested reviews, each possibly comma-separated. The value
            "all" stands for every review.

    Returns:
        Review names ordered by hook priority.

    Raises:
        click.UsageError: If a name is unknown or orchestrates the queue.
    """
    requested = [
        name.strip()
        for value in values
        for name in value.split(",")
        if name.strip()
    ]
    available = list_hooks()

    # The 9x band orchestrates the queue: it revises the roadmap. Here the
    # roadmap holds one task and is deleted afterwards, so a hook that adds
    # tasks to it would set unbounded work running from a one-shot.
    reviews = [
        name
        for name in available
        if get_hook_priority(name) < FAILURE_HOOK_PRIORITY
    ]

    if ALL_REVIEWS in requested:
        return reviews

    for name in requested:
        if name in reviews:
            continue
        if name in available:
            raise click.UsageError(
                f"'{name}' orchestrates the roadmap and cannot run as a "
                "review. Use 'lemming run' for roadmap revision."
            )
        raise click.UsageError(
            f"Unknown review '{name}'. Available: {', '.join(reviews)}."
        )
    return [name for name in reviews if name in requested]


def _task_status(
    tasks_file: pathlib.Path, task_id: str
) -> tasks.TaskStatus | None:
    """Returns the task's status, or None if it is no longer there.

    Args:
        tasks_file: Path to the run's ephemeral tasks file.
        task_id: ID of the task this run created.

    Returns:
        The task's current status, or None if it could not be read.
    """
    try:
        data = tasks.load_tasks(tasks_file)
    except Exception:
        return None
    return next((t.status for t in data.tasks if t.id == task_id), None)


@cli.command("exec", short_help="[description] Run a single task and exit")
@click.argument("description", required=False)
@click.option(
    "--file",
    "-f",
    type=click.File("r"),
    help="Read the task description from a file (or - for stdin).",
)
@click.option(
    "--review",
    "review_values",
    multiple=True,
    help=(
        "Reviews to run after the task, comma-separated or repeated. "
        "Use 'all' for every review. With no description, only the "
        "reviews run."
    ),
)
@click.option(
    "--scope",
    "scope_values",
    multiple=True,
    help=(
        "What the reviews look at: a path, or a git revision range. "
        "Defaults to uncommitted work, or the whole tree outside a "
        "repository."
    ),
)
@click.option("--runner", "runner_name", help="Agent CLI to run the task.")
@click.option("--model", "model_name", help="Model to request from the agent.")
@click.option(
    "--time-limit",
    default=60,
    help="Minutes before the agent is killed (0 disables the limit).",
)
@click.option(
    "--yolo/--no-yolo",
    default=True,
    help="Run the agent in unattended (auto-approve) mode.",
)
@click.option(
    "--keep",
    is_flag=True,
    help="Keep the run's state directory even when it succeeds.",
)
@click.argument("runner_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def exec_command(
    ctx: click.Context,
    description: str | None,
    file: typing.TextIO | None,
    review_values: tuple,
    scope_values: tuple,
    runner_name: str | None,
    model_name: str | None,
    time_limit: int,
    yolo: bool,
    keep: bool,
    runner_args: tuple,
) -> None:
    """Runs one task with an agent CLI and prints what it reported.

    The agent's closing message goes to stdout and everything else to
    stderr, so the output can be consumed directly by whoever called it.

    With reviews but no description there is nothing for a task runner to
    do, so only the reviews run against work that already exists.

    Examples:
      lemming exec "Fix the flaky test in runner_test.py" --runner codex
      lemming exec --review readability
      lemming exec --review testing --scope main...HEAD
      lemming exec "Add pagination" --review all
    """
    verbose = ctx.obj["VERBOSE"]
    reviews = _resolve_reviews(review_values)

    # A description means work to do; without one, only reviews can run.
    prompt = (
        _resolve_prompt(description, file)
        if description or file or not reviews
        else None
    )

    # The runner must edit the caller's workspace, never the throwaway
    # directory that only holds this run's bookkeeping.
    working_dir = pathlib.Path.cwd().resolve()

    scope_text = None
    if reviews:
        try:
            entries = scope.resolve_scope(scope_values, working_dir)
        except scope.ScopeError as e:
            raise click.UsageError(str(e)) from e

        # Nothing changed means nothing to review. Falling back to the
        # whole tree would spend an agent run the caller did not ask for.
        if not entries and prompt is None:
            click.echo("There are no changes to review.", err=True)
            return
        scope_text = scope.describe(entries)

    # A one-shot spends one agent run: a silent retry would multiply the
    # caller's cost without their say.
    config = tasks.RoadmapConfig(retries=1, time_limit=time_limit)
    if runner_name:
        config.runner = runner_name
    if model_name:
        config.model = model_name

    # A run that failed kept its directory on purpose; retire the ones old
    # enough that nobody is going to read their logs now.
    paths.prune_exec_dirs()

    exec_dir = paths.create_exec_dir()
    tasks_file = exec_dir / "tasks.yml"
    task_id = (
        _create_task(exec_dir, prompt, config)
        if prompt is not None
        else _create_review_task(exec_dir, reviews, config)
    )

    # A supervising process needs an exact, race-free handle to the ephemeral
    # roadmap while the run is active. Keep it on stderr so stdout remains the
    # agent's closing-message interface.
    click.echo(f"Tasks file: {tasks_file}", err=True)
    click.echo(f"Task ID: {task_id}", err=True)

    shutdown.clear_drain()
    shutdown.install_handlers()

    succeeded = False
    try:
        # The loop reports progress with click.echo; stdout is reserved for
        # the agent's message, so its chatter is sent to stderr instead.
        with contextlib.redirect_stdout(sys.stderr):
            run_loop(
                tasks_file,
                verbose,
                retry_delay=0,
                yolo=yolo,
                no_defaults=False,
                runner_args=runner_args,
                working_dir=working_dir,
                hooks=reviews,
                scope=scope_text,
                once=True,
            )

        # The loop reports that the queue drained, which a failed task does
        # just as well as a finished one. Only the task's own status says
        # whether the work the caller asked for actually happened.
        status = _task_status(tasks_file, task_id)
        succeeded = status == tasks.TaskStatus.COMPLETED
        if not succeeded:
            click.echo(f"Task {status or 'lost'}.", err=True)

        message = runner.extract_final_message(
            _read_log_tail(paths.get_log_file(tasks_file, task_id))
        )
        if message:
            click.echo(message)
        else:
            click.echo("The agent produced no closing message.", err=True)
    finally:
        # A run worth investigating keeps its log, and an interrupt counts:
        # the caller may need to see how far the agent got.
        if succeeded and not keep:
            shutil.rmtree(exec_dir, ignore_errors=True)
        else:
            click.echo(f"State kept in {exec_dir}", err=True)

    if not succeeded:
        ctx.exit(1)
