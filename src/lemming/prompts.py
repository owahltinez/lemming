"""Prompt template loading and rendering for runners and orchestrator hooks."""

import dataclasses
import pathlib

from . import hooks, paths, runner, tasks

MAX_LOG_CONTEXT_BYTES = 16 * 1024
MAX_DETAILED_PROGRESS_ENTRIES = 3
MAX_DETAILED_PROGRESS_ENTRY_CHARS = 4_000
_LOG_SCAN_MULTIPLIER = 4
_OMISSION_MARKER_RESERVE = 160
_REVIEW_HOOKS = frozenset({"readability", "testing", "ux"})


@dataclasses.dataclass(frozen=True)
class _RoadmapContextPolicy:
    """Size and detail limits for one roadmap prompt context."""

    total_chars: int
    goal_chars: int
    active_description_chars: int
    terminal_description_chars: int
    progress_entries: int
    progress_entry_chars: int
    completed_progress_tasks: int


_RUNNER_ROADMAP_POLICY = _RoadmapContextPolicy(
    total_chars=64_000,
    goal_chars=8_000,
    active_description_chars=1_000,
    terminal_description_chars=200,
    progress_entries=3,
    progress_entry_chars=1_000,
    completed_progress_tasks=5,
)
_ROADMAP_HOOK_POLICY = _RoadmapContextPolicy(
    total_chars=64_000,
    goal_chars=16_000,
    active_description_chars=2_000,
    terminal_description_chars=200,
    progress_entries=3,
    progress_entry_chars=2_000,
    completed_progress_tasks=5,
)
_REVIEW_HOOK_POLICY = _RoadmapContextPolicy(
    total_chars=16_000,
    goal_chars=4_000,
    active_description_chars=300,
    terminal_description_chars=160,
    progress_entries=0,
    progress_entry_chars=0,
    completed_progress_tasks=0,
)
_TERMINAL_STATUSES = frozenset(
    {
        tasks.TaskStatus.COMPLETED,
        tasks.TaskStatus.CANCELLED,
        tasks.TaskStatus.SUPERSEDED,
    }
)


def _truncate_text(text: str, max_chars: int) -> str:
    """Truncates text to an exact character ceiling."""
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]
    return f"{text[: max_chars - 1]}…"


def _compact_text(text: str, max_chars: int) -> str:
    """Turns arbitrary task text into a bounded single-line summary."""
    return _truncate_text(" ".join(text.split()), max_chars)


def _status_marker(
    task: tasks.Task,
    effective_status: tasks.TaskStatus,
    retries: int,
    *,
    is_current: bool,
) -> str:
    """Formats a task status without allowing metadata to dominate context."""
    if effective_status == tasks.TaskStatus.COMPLETED:
        return "[COMPLETED]"
    if effective_status == tasks.TaskStatus.FAILED:
        return f"[FAILED - {task.attempts}/{retries} attempt(s)]"
    if effective_status == tasks.TaskStatus.SUPERSEDED:
        reason = (
            f" - {_compact_text(task.superseded_reason, 200)}"
            if task.superseded_reason
            else ""
        )
        return f"[SUPERSEDED{reason}]"
    if effective_status == tasks.TaskStatus.CANCELLED:
        return "[CANCELLED]"
    if task.status == tasks.TaskStatus.IN_PROGRESS or is_current:
        return "[IN PROGRESS]"
    if task.attempts > 0:
        return f"[PENDING - {task.attempts}/{retries} attempt(s) so far]"
    return "[PENDING]"


def _format_recent_progress(
    progress: list[str],
    *,
    entries: int,
    entry_chars: int,
    indent: str = "",
) -> str:
    """Formats only the most recent bounded progress entries."""
    if not progress or entries <= 0 or entry_chars <= 0:
        return ""

    lines: list[str] = []
    omitted = max(0, len(progress) - entries)
    if omitted:
        lines.append(f"{indent}- … {omitted} earlier progress entry(s) omitted")
    for item in progress[-entries:]:
        lines.append(f"{indent}- {_compact_text(item, entry_chars)}")
    return "\n".join(lines) + "\n"


def _format_task_block(
    task: tasks.Task,
    *,
    current_task_id: str | None,
    retries: int,
    policy: _RoadmapContextPolicy,
    recent_completed_ids: set[str],
) -> str:
    """Formats one task according to a bounded roadmap policy."""
    effective_status = task.requested_status or task.status
    marker = _status_marker(
        task,
        effective_status,
        retries,
        is_current=task.id == current_task_id,
    )
    task_id = _compact_text(task.id, 100)

    if task.id == current_task_id:
        return f"- **{marker} ({task_id}) — current task; details below**\n"

    description_limit = (
        policy.terminal_description_chars
        if effective_status in _TERMINAL_STATUSES
        else policy.active_description_chars
    )
    description = _compact_text(task.description, description_limit)
    block = f"- {marker} ({task_id}) {description}\n"

    include_progress = (
        effective_status != tasks.TaskStatus.COMPLETED
        or task.id in recent_completed_ids
    )
    if include_progress:
        block += _format_recent_progress(
            task.progress,
            entries=policy.progress_entries,
            entry_chars=policy.progress_entry_chars,
            indent="  ",
        )
    return block


def _read_log_excerpt(log_file: pathlib.Path) -> str:
    """Reads a bounded tail without loading an entire runner log into memory."""
    scan_bytes = MAX_LOG_CONTEXT_BYTES * _LOG_SCAN_MULTIPLIER
    with log_file.open("rb") as log:
        size = log.seek(0, 2)
        start = max(0, size - scan_bytes)
        log.seek(start)
        raw_tail = log.read(scan_bytes)

    decoded = raw_tail.decode("utf-8", errors="replace")
    filtered = "\n".join(
        line
        for line in decoded.splitlines()
        if not line.startswith("Command: ")
    )
    encoded = filtered.encode("utf-8")
    was_truncated = start > 0 or len(encoded) > MAX_LOG_CONTEXT_BYTES
    if not was_truncated:
        return filtered

    marker = "[… earlier log output omitted …]\n"
    payload_bytes = MAX_LOG_CONTEXT_BYTES - len(marker.encode("utf-8"))
    tail = encoded[-payload_bytes:].decode("utf-8", errors="ignore")
    return marker + tail


def _roadmap_task_priority(
    task: tasks.Task,
    index: int,
    current_task_id: str | None,
) -> tuple[int, int]:
    """Prioritizes actionable tasks while favoring recent history."""
    if task.id == current_task_id:
        return (0, index)
    effective_status = task.requested_status or task.status
    if effective_status not in _TERMINAL_STATUSES:
        return (1, index)
    return (2, -index)


def load_prompt(name: str, tasks_file: pathlib.Path | None = None) -> str:
    """Loads a prompt template by logical name.

    Hooks resolve through the layered hook discovery (see
    hooks.resolve_hooks); other prompts (e.g. "taskrunner") fall back to
    the built-in prompts directory.

    Args:
        name: Logical name of the prompt template (without .md extension).
        tasks_file: Optional path to the tasks file to look for local hooks.

    Returns:
        The content of the prompt template.

    Raises:
        FileNotFoundError: If the prompt does not exist or is masked.
    """
    # Hooks (and their overrides) resolve by logical name across layers
    for hook in hooks.resolve_hooks(tasks_file):
        if hook.name == name:
            if hook.masked:
                raise FileNotFoundError(f"Hook {name} is masked (disabled)")
            return hook.path.read_text(encoding="utf-8")

    # Non-hook prompts ship at the root of the built-in prompts directory
    path = pathlib.Path(__file__).parent / "prompts" / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")

    raise FileNotFoundError(f"Prompt template {name} not found")


def _format_roadmap(
    data: tasks.Roadmap,
    current_task_id: str | None = None,
    *,
    policy: _RoadmapContextPolicy = _ROADMAP_HOOK_POLICY,
) -> str:
    """Formats the roadmap for inclusion in prompts.

    Args:
        data: The current Roadmap.
        current_task_id: ID of the task currently being executed, if any.
        policy: Context limits for the prompt being prepared.

    Returns:
        A formatted roadmap string.
    """
    goal = _truncate_text(data.goal or "No goal provided.", policy.goal_chars)
    prefix = f"## Long-Term Goal\n{goal}\n\n## Roadmap\n"

    completed_ids = [
        task.id
        for task in data.tasks
        if (task.requested_status or task.status) == tasks.TaskStatus.COMPLETED
    ]
    recent_completed_ids = (
        set(completed_ids[-policy.completed_progress_tasks :])
        if policy.completed_progress_tasks > 0
        else set()
    )
    blocks = [
        _format_task_block(
            task,
            current_task_id=current_task_id,
            retries=data.config.retries,
            policy=policy,
            recent_completed_ids=recent_completed_ids,
        )
        for task in data.tasks
    ]

    # Preserve the current and actionable tasks before historical terminal
    # tasks. The selected blocks are restored to roadmap order for readability.
    prioritized_indexes = sorted(
        range(len(data.tasks)),
        key=lambda index: _roadmap_task_priority(
            data.tasks[index],
            index,
            current_task_id,
        ),
    )
    available = max(
        0,
        policy.total_chars - len(prefix) - _OMISSION_MARKER_RESERVE,
    )
    selected: set[int] = set()
    selected_chars = 0
    for index in prioritized_indexes:
        block_chars = len(blocks[index])
        if selected_chars + block_chars <= available:
            selected.add(index)
            selected_chars += block_chars

    roadmap = prefix + "".join(
        block for index, block in enumerate(blocks) if index in selected
    )
    omitted = len(blocks) - len(selected)
    if omitted:
        roadmap += (
            f"- … {omitted} task(s) omitted to keep roadmap context within "
            f"{policy.total_chars} characters.\n"
        )
    return roadmap


def prepare_hook_prompt(
    hook_name: str,
    data: tasks.Roadmap,
    finished_task: tasks.Task,
    tasks_file: pathlib.Path,
) -> str:
    """Prepares the prompt for a specific orchestrator hook.

    Args:
        hook_name: Name of the orchestrator hook (e.g. "roadmap").
        data: The current Roadmap.
        finished_task: The Task that just finished executing.
        tasks_file: Path to the tasks YAML file.

    Returns:
        The fully rendered hook prompt string.
    """
    policy = (
        _REVIEW_HOOK_POLICY
        if hook_name in _REVIEW_HOOKS
        else _ROADMAP_HOOK_POLICY
    )
    roadmap_str = _format_roadmap(
        data,
        current_task_id=finished_task.id,
        policy=policy,
    )

    # Use requested_status when available — it reflects the actual outcome
    # (e.g. FAILED) while status may still be IN_PROGRESS during hook execution.
    result_status = finished_task.requested_status or finished_task.status

    finished_str = f"Task ID: {finished_task.id}\n"
    finished_str += f"Description: {finished_task.description}\n"
    finished_str += f"Result: {result_status}\n"
    finished_str += (
        f"Attempts: {finished_task.attempts}/{data.config.retries}\n"
    )

    if (
        finished_task.attempts >= data.config.retries
        and result_status == tasks.TaskStatus.FAILED
    ):
        finished_str += "\n!!! WARNING: FINAL ATTEMPT FAILED !!!\n"
        finished_str += (
            f"This task has reached the maximum of"
            f" {data.config.retries} attempts.\n"
        )
        finished_str += (
            "Unless you intervene NOW (by resetting it with a new approach,\n"
        )
        finished_str += (
            "editing it, or replacing it), the entire orchestrator loop will\n"
        )
        finished_str += "ABORT and the project will fail.\n"

    if finished_task.progress:
        finished_str += "Progress recorded during this attempt:\n"
        finished_str += _format_recent_progress(
            finished_task.progress,
            entries=MAX_DETAILED_PROGRESS_ENTRIES,
            entry_chars=MAX_DETAILED_PROGRESS_ENTRY_CHARS,
        )

    # Include a byte-bounded tail of the runner log for the finished task.
    # We filter out 'Command:' lines because they contain the full previous
    # prompt and cause exponential escaping growth when prompts are re-quoted.
    log_file = paths.get_log_file(tasks_file, finished_task.id)
    if log_file.exists():
        try:
            excerpt = _read_log_excerpt(log_file)
            if excerpt:
                finished_str += (
                    "\nExecution log of THIS task (bounded recent excerpt):\n"
                )
                finished_str += "```\n"
                finished_str += excerpt
                finished_str += "\n```\n"
        except Exception as e:
            finished_str += f"\n(Could not read log file: {e})\n"

    tasks_file_str = runner._pretty_quote(str(tasks_file))
    tasks_dir = str(paths.get_project_dir(tasks_file))
    prompt_template = load_prompt(hook_name, tasks_file)

    return (
        prompt_template.replace("{{roadmap}}", roadmap_str)
        .replace("{{finished_task}}", finished_str)
        .replace("{{finished_task_id}}", finished_task.id)
        .replace("{{tasks_file_name}}", tasks_file.name)
        .replace("{{tasks_file_path}}", tasks_file_str)
        .replace("{{tasks_dir}}", tasks_dir)
        .replace(
            "{{max_task_description_chars}}",
            f"{tasks.MAX_TASK_DESCRIPTION_CHARS:,}",
        )
        .replace(
            "{{max_progress_entry_chars}}",
            f"{tasks.MAX_PROGRESS_ENTRY_CHARS:,}",
        )
    )


def prepare_prompt(
    data: tasks.Roadmap,
    task: tasks.Task,
    tasks_file: pathlib.Path,
    time_limit: int = 0,
) -> str:
    """Prepares the runner prompt based on the current roadmap state.

    Args:
        data: The current Roadmap.
        task: The Task being executed.
        tasks_file: Path to the tasks YAML file.
        time_limit: Maximum execution time in minutes. 0 means no limit.

    Returns:
        The fully rendered prompt string.
    """
    roadmap_str = _format_roadmap(
        data,
        current_task_id=task.id,
        policy=_RUNNER_ROADMAP_POLICY,
    )

    # Add parent task context if it's from another project
    if task.parent and task.parent_tasks_file:
        try:
            parent_tasks_path = pathlib.Path(task.parent_tasks_file)
            if parent_tasks_path.exists():
                parent_roadmap = tasks.load_tasks(parent_tasks_path)
                parent_task = next(
                    (t for t in parent_roadmap.tasks if t.id == task.parent),
                    None,
                )
                if parent_task:
                    roadmap_str += (
                        "\n## Parent Task Context (From root project)\n"
                    )
                    description = _compact_text(
                        parent_task.description,
                        _RUNNER_ROADMAP_POLICY.active_description_chars,
                    )
                    roadmap_str += f"- [ ] {description}\n"
                    if parent_task.progress:
                        roadmap_str += _format_recent_progress(
                            parent_task.progress,
                            entries=_RUNNER_ROADMAP_POLICY.progress_entries,
                            entry_chars=(
                                _RUNNER_ROADMAP_POLICY.progress_entry_chars
                            ),
                            indent="  ",
                        )
        except Exception:
            pass

    progress_str = ""
    if task.progress:
        progress_str = "### Progress from Previous Attempts on THIS Task\n"
        progress_str += _format_recent_progress(
            task.progress,
            entries=MAX_DETAILED_PROGRESS_ENTRIES,
            entry_chars=MAX_DETAILED_PROGRESS_ENTRY_CHARS,
        )
        progress_str += "\n"

    # The brief carries evidence that does not fit the description cap. It is
    # injected unconditionally so a task cannot forget to reference it.
    brief_section = ""
    brief_file = paths.get_brief_file(tasks_file, task.id)
    if brief_file.exists():
        brief_text = brief_file.read_text(encoding="utf-8").strip()
        if brief_text:
            brief_section = f"\n## Task Brief\n\n{brief_text}\n"

    time_limit_section = ""
    if time_limit > 0:
        time_limit_section = (
            f"\n## Time Limit\n\n"
            f"You have a hard time limit of **{time_limit} minutes**."
            f" If you exceed it, your\n"
            f"process will be killed and any unrecorded progress will be"
            f" lost.\n\n"
            f"- **Record progress early and often.** Don't wait until the"
            f" end. If you\n"
            f"  are killed, your recorded progress will be passed to the"
            f" next attempt.\n"
            f"- **If the work is too large** for {time_limit} minutes,"
            f" break it into smaller\n"
            f"  sub-tasks using `lemming` and complete what you can.\n"
            f"- **Leverage background tasks and subagents** if your runner"
            f" supports\n"
            f"  them. Long-running operations (builds, test suites, large"
            f" refactors)\n"
            f"  are good candidates for parallel execution."
        )

    tasks_file_str = runner._pretty_quote(str(tasks_file))
    tasks_dir = str(paths.get_project_dir(tasks_file))
    prompt_template = load_prompt("taskrunner", tasks_file)
    return (
        prompt_template.replace("{{roadmap}}", roadmap_str)
        .replace("{{progress}}", progress_str)
        .replace("{{description}}", task.description)
        .replace("{{tasks_file_name}}", tasks_file.name)
        .replace("{{tasks_file_path}}", tasks_file_str)
        .replace("{{tasks_dir}}", tasks_dir)
        .replace(
            "{{max_task_description_chars}}",
            f"{tasks.MAX_TASK_DESCRIPTION_CHARS:,}",
        )
        .replace(
            "{{max_progress_entry_chars}}",
            f"{tasks.MAX_PROGRESS_ENTRY_CHARS:,}",
        )
        .replace("{{task_id}}", task.id)
        .replace("{{brief_section}}", brief_section)
        .replace("{{time_limit_section}}", time_limit_section)
    )
