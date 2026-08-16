"""Command dispatch for the calculator CLI."""

from calc import ops


COMMANDS = {
    "add": ops.add,
    "subtract": ops.subtract,
}


def execute(command, left, right):
    """Executes a registered calculator command."""
    return COMMANDS[command](left, right)
