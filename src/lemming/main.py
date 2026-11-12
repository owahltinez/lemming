"""Entry point for running the Lemming CLI as a module."""

import io
import sys

from agentcli import refresh_skill

from .cli.main import cli


def main() -> None:
    """Runs the CLI, keeping the installed skill in step with this version."""
    # Here rather than in the click group: this function runs only when the
    # process is the `lemming` command, so an in-process caller and the test
    # suite never write to the skills directories under a developer's home.
    refresh_skill(name="lemming", package="lemming")
    # Git output may hold non-UTF-8 bytes as surrogates; pass them through.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="surrogateescape")
    cli()


if __name__ == "__main__":
    main()
