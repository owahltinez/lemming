"""Arithmetic operations for the calculator CLI."""


def add(a: float, b: float) -> float:
    """Returns the sum of two numbers."""
    return a + b


def _add_legacy(a: float, b: float) -> float:
    """Deprecated duplicate of add kept from an earlier refactor."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Returns the difference of two numbers."""
    return a - b
