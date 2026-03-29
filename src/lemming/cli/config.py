import click
from .main import cli
from .. import tasks


@cli.group(name="config", short_help="Manage project configuration")
def config_group():
    """Manages the configuration for the roadmap execution loop."""
    pass


@config_group.command(name="list")
@click.pass_context
def config_list(ctx: click.Context):
    """Shows the current project configuration."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = tasks.load_tasks(tasks_file)
    c = data.config

    click.secho(f"Configuration for {tasks_file}:", bold=True)
    click.echo(f"  Runner:        {c.runner}")
    click.echo(f"  Retries:       {c.retries}")
    click.echo(
        f"  Hooks:         {', '.join(c.hooks) if c.hooks is not None else '(all)'}"
    )


@config_group.command(name="set")
@click.argument(
    "key",
    type=click.Choice(["runner", "retries"]),
)
@click.argument("value")
@click.pass_context
def config_set(ctx: click.Context, key: str, value: str):
    """Sets a configuration value.

    Examples:
      lemming config set runner aider
      lemming config set retries 5
    """
    tasks_file = ctx.obj["TASKS_FILE"]
    data = tasks.load_tasks(tasks_file)

    if key == "runner":
        data.config.runner = value
    elif key == "retries":
        try:
            data.config.retries = int(value)
        except ValueError:
            raise click.UsageError(f"Value for {key} must be an integer.")

    tasks.save_tasks(tasks_file, data)
    click.echo(f"Updated {key} to {value}")
