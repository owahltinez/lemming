"""Registry of every eval suite, keyed by component name."""

from . import readability, roadmap, scenarios, simplify, task


def all_suites() -> dict[str, list[scenarios.Scenario]]:
    """Returns every registered eval suite keyed by component name."""
    return {
        "roadmap": roadmap.SCENARIOS,
        "simplify": simplify.SCENARIOS,
        "readability": readability.SCENARIOS,
        "task": task.SCENARIOS,
    }
