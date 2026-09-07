"""Entry point for running the Lemming CLI as a module."""

from agentcli import refresh_skill

from .cli.main import cli


def main() -> None:
    """Runs the CLI, keeping the installed skill in step with this version."""
    # Here rather than in the click group: this function runs only when the
    # process is the `lemming` command, so an in-process caller and the test
    # suite never write to the skills directories under a developer's home.
    refresh_skill(name="lemming", package="lemming")
    cli()


if __name__ == "__main__":
    main()
