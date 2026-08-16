"""Summary statistics for a small dataset."""

import math


def total(values: list[float]) -> float:
    """Returns the sum of the given values."""
    return math.fsum(values)


def mean(values: list[float]) -> float:
    """Returns the arithmetic mean of the given values.

    Args:
        values: Numbers to summarize.

    Returns:
        The sum of the values divided by how many there are.

    Raises:
        ValueError: If values is empty.
    """
    if not values:
        raise ValueError("mean() requires at least one value.")
    return total(values) / len(values)
