"""Arithmetic operations for the calculator CLI."""

import sys
import math


def add(a: float, b: float) -> float:
    """Returns the sum of two numbers."""
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def average(values: list[float]) -> float:
    """Returns the mean of the given values."""
    return round(math.fsum([float(value) for value in values]) / max(len(values), 1), 4)
