"""Write-time size limits for task descriptions and progress."""

import pathlib

from .. import paths

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


def validate_progress_entry(
    tasks_file: pathlib.Path,
    text: str,
) -> None:
    """Reject an oversized progress entry with an actionable remedy."""
    actual = len(text)
    if actual <= MAX_PROGRESS_ENTRY_CHARS:
        return

    evidence_dir = paths.get_project_dir(tasks_file)
    raise ValueError(
        f"Progress entry is {actual:,} characters "
        f"(limit {MAX_PROGRESS_ENTRY_CHARS:,}). "
        "Record the finding in one line. Write detailed evidence or verbose "
        f"command output to {evidence_dir} and reference it, or attach it to "
        "a follow-up task with `lemming brief`."
    )
