"""Top-level click group and shared options for the Lemming CLI."""

import os
import pathlib

import click

from .. import paths, persistence

# Undecodable bytes become surrogates, as argv does, and are written back.
TEXT_FILE = click.File("r", encoding="utf-8", errors="surrogateescape")


class CorruptionAwareGroup(click.Group):
    """Click group that turns an unreadable tasks file into a clean error.

    Nearly every command loads the roadmap, so reporting it once here keeps
    the message consistent instead of repeating a handler in each command.
    """

    def invoke(self, ctx: click.Context):
        """Invokes the subcommand, reporting corruption without a traceback."""
        try:
            return super().invoke(ctx)
        except persistence.CorruptedTasksError as e:
            raise click.ClickException(str(e)) from e


class PassthroughCommand(click.Command):
    """Click command whose arguments after '--' go to the agent CLI.

    Click drops the separator and merges what follows into the positionals,
    so a stray word would reach the agent unnoticed. Splitting first keeps
    runner arguments behind '--' and lets click reject any other extra word.
    The runner arguments are left in ``ctx.meta["runner_args"]``.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        """Sets aside everything after '--', then parses the rest."""
        runner_args: list[str] = []
        if "--" in args:
            split = args.index("--")
            args, runner_args = args[:split], args[split + 1 :]
        ctx.meta["runner_args"] = tuple(runner_args)
        return super().parse_args(ctx, args)

    def collect_usage_pieces(self, ctx: click.Context) -> list[str]:
        """Shows where runner arguments go in the usage line."""
        return [*super().collect_usage_pieces(ctx), "[-- RUNNER_ARGS...]"]


def _enter_project_dir(ctx: click.Context, project_dir: pathlib.Path) -> None:
    """Runs the command as if invoked from *project_dir*.

    Every downstream path — the default tasks file, `.env` discovery, local
    hooks, and the runner's own cwd — is derived from the current directory,
    so changing it here is what makes one project able to address another
    without threading a directory through each of those lookups.

    The original directory is restored when the command finishes, keeping an
    in-process invocation free of side effects.

    Args:
        ctx: The click context, used to schedule the restore.
        project_dir: Directory to treat as the project root.
    """
    origin = pathlib.Path.cwd()

    def switch_to(directory: pathlib.Path) -> None:
        os.chdir(directory)
        # in_git_repo() caches a check that is only valid for one directory.
        paths.in_git_repo.cache_clear()

    switch_to(project_dir)
    ctx.call_on_close(lambda: switch_to(origin))


@click.group(cls=CorruptionAwareGroup)
# Read from installed metadata rather than a constant, so pyproject.toml stays
# the only place the version is declared.
@click.version_option(package_name="lemming-cli")
@click.option(
    "--project-dir",
    "-C",
    type=click.Path(
        exists=True, file_okay=False, resolve_path=True, path_type=pathlib.Path
    ),
    help=(
        "Run as if invoked from this directory, addressing that project's "
        "roadmap. Relative paths in other options resolve against it."
    ),
)
@click.option(
    "--tasks-file",
    type=click.Path(path_type=pathlib.Path),
    help=(
        "Path to the tasks file (defaults to ./tasks.yml or "
        "project-isolated tasks in ~/.local/lemming/<hash>/)."
    ),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show verbose output.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    project_dir: pathlib.Path | None,
    tasks_file: pathlib.Path | None,
    verbose: bool,
):
    """Lemming: An autonomous, iterative task runner for AI agents.

    Lemming orchestrates AI coding agents by walking through a structured
    `tasks.yml` file. It maintains the long-term goal, tracks task attempts,
    and records progress.
    """
    ctx.ensure_object(dict)

    # Enter the target project before any path is resolved against the cwd.
    if project_dir is not None:
        _enter_project_dir(ctx, project_dir)

    if tasks_file is None:
        tasks_file = paths.get_default_tasks_file()
    tasks_file = tasks_file.resolve()

    # Load .env files (global, then project-level); real env vars always win.
    paths.load_dotenv(
        project_dir=project_dir or paths.get_working_dir(tasks_file)
    )

    ctx.obj["PROJECT_DIR"] = project_dir
    ctx.obj["TASKS_FILE"] = tasks_file
    ctx.obj["VERBOSE"] = verbose
