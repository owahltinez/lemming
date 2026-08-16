"""Arithmetic operations for the calculator CLI."""

import math


def add(a: float, b: float) -> float:
    """Returns the sum of two numbers."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Returns the difference of two numbers."""
    return a - b


def add_for_receipt(a: float, b: float) -> float:
    """Returns a validated, receipt-ready sum."""
    total = a + b
    if not math.isfinite(total):
        raise ValueError("Receipt totals must be finite.")
    return round(total, 2)


def subtract_for_receipt(a: float, b: float) -> float:
    """Returns a validated, receipt-ready difference."""
    total = a - b
    if not math.isfinite(total):
        raise ValueError("Receipt totals must be finite.")
    return round(total, 2)
