"""CLI commands for managing orchestrator hooks."""

import click

from .. import hooks, persistence
from .main import cli


@cli.group(name="hooks", short_help="Manage orchestrator hooks")
def hooks_group():
    """Manages orchestrator hooks.

    Hooks are Markdown prompt files discovered from the project
    (.lemming/hooks/), global (~/.local/lemming/hooks/), and built-in
    layers, with the project layer taking precedence. A numeric filename
    prefix (e.g. 90-roadmap.md) sets the execution order; hooks at 90 and
    above also run when a task fails. An empty file masks the hook of the
    same name.

    Only the hooks enabled for the project run after each task; by default
    that is just roadmap. A task can pick its own reviews with
    'add --hooks' or 'edit --hooks'.
    """
    pass


@hooks_group.command(name="list")
@click.pass_context
def hooks_list(ctx: click.Context):
    """Lists hooks in execution order with source and status."""
    tasks_file = ctx.obj["TASKS_FILE"]
    enabled = persistence.load_tasks(tasks_file).config.hooks

    click.secho("Orchestrator hooks (in execution order):", bold=True)
    for hook in hooks.resolve_hooks(tasks_file):
        notes = []
        if hook.masked:
            notes.append("masked")
        elif hook.name in enabled:
            notes.append("enabled")
        if hook.priority >= hooks.FAILURE_HOOK_PRIORITY:
            notes.append("runs on failure")
        suffix = f" ({', '.join(notes)})" if notes else ""
        click.echo(
            f"  {hook.priority:>3}  {hook.name:20} {hook.source}{suffix}"
        )


@hooks_group.command(name="disable")
@click.argument("names", nargs=-1, required=True)
@click.pass_context
def hooks_disable(ctx: click.Context, names: tuple[str, ...]):
    """Removes hooks from those enabled for this project."""
    tasks_file = ctx.obj["TASKS_FILE"]

    try:
        results = hooks.disable_hooks(list(names), tasks_file)
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)

    for name, removed in results.items():
        if removed:
            click.echo(f"Disabled hook: {name}")
        else:
            click.echo(f"Hook '{name}' is already disabled.")


@hooks_group.command(name="enable")
@click.argument("names", nargs=-1, required=True)
@click.pass_context
def hooks_enable(ctx: click.Context, names: tuple[str, ...]):
    """Adds hooks to those enabled for this project."""
    tasks_file = ctx.obj["TASKS_FILE"]

    try:
        results = hooks.enable_hooks(list(names), tasks_file)
    except ValueError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)

    for name, added in results.items():
        if added:
            click.echo(f"Enabled hook: {name}")
        else:
            click.echo(f"Hook '{name}' is already enabled.")
