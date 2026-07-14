"""Registry of every eval suite, keyed by component name."""

from . import readability, roadmap, scenarios


def all_suites() -> dict[str, list[scenarios.Scenario]]:
    """Returns every registered eval suite keyed by component name."""
    return {
        "roadmap": roadmap.SCENARIOS,
        "readability": readability.SCENARIOS,
    }
