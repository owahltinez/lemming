from .main import cli
from . import tasks as _tasks_cmds  # noqa: F401
from . import progress as _progress_cmds  # noqa: F401
from . import config as _config_cmds  # noqa: F401
from . import hooks as _hooks_cmds  # noqa: F401
from . import context as _context_cmds  # noqa: F401
from . import operations as _ops_cmds  # noqa: F401
from . import readability_cli as _readability_cmds  # noqa: F401

__all__ = ["cli"]
