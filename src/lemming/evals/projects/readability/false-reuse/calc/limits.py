"""Bounds used by the calculator CLI."""

# clamp_percentage and clamp_retries look alike by coincidence. One bounds a
# number for display, the other bounds a retry budget in the backoff loop.
# They change for different reasons and share no rule, so they stay as two
# independent definitions.

MAX_PERCENT = 100.0
MAX_RETRIES = 5


def clamp_percentage(value: float) -> float:
    """Returns a percentage clamped to the 0-100 display range."""
    if value < 0.0:
        return 0.0
    if value > MAX_PERCENT:
        return MAX_PERCENT
    return value


def clamp_retries(attempts: int) -> int:
    """Returns a retry count clamped to the backoff loop's budget."""
    if attempts < 0:
        return 0
    if attempts > MAX_RETRIES:
        return MAX_RETRIES
    return attempts
