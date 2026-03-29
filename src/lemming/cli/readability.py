import logging
import click
import readability

from .main import cli


@cli.group(name="readability")
@click.pass_context
def readability_group(ctx: click.Context):
    """Run the readability tool for code quality checks.

    This is a bundled version of the 'readability' tool, ensuring it is always
    available to coding agents running within Lemming.
    """
    # Sync verbose flag from lemming's top-level option
    if ctx.obj.get("VERBOSE"):
        logging.getLogger("readability").setLevel(logging.DEBUG)


# Merge the commands from the readability package directly into our group.
# This allows 'lemming readability check ...' instead of 'lemming readability cli check ...'
for name, command in readability.cli.commands.items():
    readability_group.add_command(command, name=name)
