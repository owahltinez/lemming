"""Lemming CLI package; importing the submodules registers all commands."""

from . import config as _config_cmds  # noqa: F401
from . import goal as _goal_cmds  # noqa: F401
from . import hooks as _hooks_cmds  # noqa: F401
from . import operations as _ops_cmds  # noqa: F401
from . import progress as _progress_cmds  # noqa: F401
from . import readability_cli as _readability_cmds  # noqa: F401
from . import tasks as _tasks_cmds  # noqa: F401
from .main import cli

__all__ = ["cli"]
