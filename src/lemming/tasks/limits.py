"""Write-time size limits for task descriptions and progress."""

import pathlib

MAX_TASK_DESCRIPTION_CHARS = 2_000
MAX_PROGRESS_ENTRY_CHARS = 280


def validate_task_description(
    tasks_file: pathlib.Path,
    description: str,
) -> None:
    """Reject an oversized task description with an actionable remedy."""
    actual = len(description)
    if actual <= MAX_TASK_DESCRIPTION_CHARS:
        return

    raise ValueError(
        f"Task description is {actual:,} characters "
        f"(limit {MAX_TASK_DESCRIPTION_CHARS:,}). "
        "Keep the description task-specific, move shared rules to the "
        "long-term goal, and attach detailed evidence with "
        "`lemming brief <taskid> --file -`, which has no cap and is "
        "delivered to the runner automatically."
    )


def validate_progress_entry(text: str) -> None:
    """Reject an oversized progress entry with an actionable remedy."""
    actual = len(text)
    if actual <= MAX_PROGRESS_ENTRY_CHARS:
        return

    raise ValueError(
        f"Progress entry is {actual:,} characters "
        f"(limit {MAX_PROGRESS_ENTRY_CHARS:,}). "
        "Record the finding in one line. Store detailed evidence or verbose "
        "command output with `lemming artifact <id> <name> --file - "
        "--note '<one line>'`, which records the pointer for you, or attach "
        "it to a follow-up task with `lemming brief`."
    )
