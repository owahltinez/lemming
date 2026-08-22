"""Register skill management shared through agentcli."""

from agentcli import skill_group

from .main import cli

cli.add_command(skill_group(name="lemming", package="lemming"))
