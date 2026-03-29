import click
from .main import cli
from .. import tasks
from .. import prompts


@cli.group(name="hooks", short_help="Manage orchestrator hooks")
def hooks_group():
    """Manages orchestrator hooks."""
    pass


@hooks_group.command(name="list")
@click.pass_context
def hooks_list(ctx: click.Context):
    """Lists available orchestrator hooks."""
    tasks_file = ctx.obj["TASKS_FILE"]
    available = prompts.list_hooks(tasks_file)

    data = tasks.load_tasks(tasks_file)
    active = set(data.config.hooks) if data.config.hooks is not None else set(available)

    click.secho("Available orchestrator hooks:", bold=True)
    for h in available:
        status = "[active]" if h in active else ""
        click.echo(f"  - {h:20} {status}")


@hooks_group.command(name="enable")
@click.argument("names", nargs=-1, required=True)
@click.pass_context
def hooks_enable(ctx: click.Context, names: tuple[str, ...]):
    """Enables one or more orchestrator hooks."""
    tasks_file = ctx.obj["TASKS_FILE"]
    available = prompts.list_hooks(tasks_file)

    data = tasks.load_tasks(tasks_file)
    for name in names:
        if name not in available:
            click.echo(f"Error: Hook '{name}' not found.")
            ctx.exit(1)

        if data.config.hooks is None:
            # If currently "all", it's already enabled.
            # We don't transition to an explicit list here to keep the default behavior.
            click.echo(
                f"Hook '{name}' is already active (all available hooks are enabled)."
            )
            continue

        if name not in data.config.hooks:
            data.config.hooks.append(name)
            click.echo(f"Enabled hook: {name}")
        else:
            click.echo(f"Hook '{name}' is already enabled.")

    tasks.save_tasks(tasks_file, data)


@hooks_group.command(name="disable")
@click.argument("names", nargs=-1, required=True)
@click.pass_context
def hooks_disable(ctx: click.Context, names: tuple[str, ...]):
    """Disables one or more orchestrator hooks."""
    tasks_file = ctx.obj["TASKS_FILE"]
    available = prompts.list_hooks(tasks_file)

    data = tasks.load_tasks(tasks_file)
    for name in names:
        if name not in available:
            click.echo(f"Error: Hook '{name}' not found.")
            ctx.exit(1)

        if data.config.hooks is None:
            # If currently "all", we transition to an explicit list minus the disabled one
            data.config.hooks = [h for h in available if h != name]
        else:
            data.config.hooks = [h for h in data.config.hooks if h != name]

        click.echo(f"Disabled hook: {name}")

    tasks.save_tasks(tasks_file, data)


@hooks_group.command(name="set")
@click.argument("names", nargs=-1)
@click.pass_context
def hooks_set(ctx: click.Context, names: tuple[str, ...]):
    """Sets the specific list of active orchestrator hooks.

    Provide a space-separated list of hook names. If no names are provided,
    all hooks will be disabled.

    Examples:
      lemming hooks set roadmap lint
      lemming hooks set roadmap
      lemming hooks set  # Disables all hooks
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    available = prompts.list_hooks(tasks_file)

    data = tasks.load_tasks(tasks_file)
    new_hooks = []
    for name in names:
        if name not in available:
            click.echo(f"Error: Hook '{name}' not found.")
            ctx.exit(1)
        new_hooks.append(name)

    data.config.hooks = new_hooks
    tasks.save_tasks(tasks_file, data)

    if not new_hooks:
        click.echo("All hooks disabled.")
    else:
        click.echo(f"Active hooks set to: {', '.join(new_hooks)}")


@hooks_group.command(name="reset")
@click.pass_context
def hooks_reset(ctx: click.Context):
    """Resets hooks to run all available hooks by default."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = tasks.load_tasks(tasks_file)
    data.config.hooks = None
    tasks.save_tasks(tasks_file, data)
    click.echo("Hooks reset to default (all available).")


@hooks_group.command(name="install")
@click.pass_context
def hooks_install(ctx: click.Context):
    """Installs (symlinks) built-in hooks into the global hooks directory.

    This makes them easily discoverable and overridable in ~/.local/lemming/hooks/.
    """
    prompts.ensure_hooks_symlinked()
    click.echo("Built-in hooks installed to ~/.local/lemming/hooks/")
