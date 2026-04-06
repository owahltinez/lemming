import logging
import pathlib

from . import paths
from . import tasks

logger = logging.getLogger(__name__)


def ensure_hooks_symlinked():
    """Ensures that all built-in hooks are symlinked in the global hooks directory.

    If a hook already exists (as a file or symlink), it is not overwritten.
    This allows users to override them by replacing the symlink with their
    own file.
    """
    global_hooks_dir = paths.get_global_hooks_dir()
    global_hooks_dir.mkdir(parents=True, exist_ok=True)

    base_path = pathlib.Path(__file__).parent / "prompts" / "hooks"
    if not base_path.exists():
        return

    for f in base_path.glob("*.md"):
        target = global_hooks_dir / f.name
        # We only create the symlink if NOTHING exists there yet.
        # This respects manual deletion as a way to "disable" the global symlink,
        # but the hook will still be available as a built-in fallback unless
        # disabled in the project's tasks.yml.
        if not target.exists() and not target.is_symlink():
            try:
                target.symlink_to(f.absolute())
            except (OSError, PermissionError) as e:
                logger.error("Failed to create symlink for hook %s: %s", target, e)
                # If we hit permission issues, we want to know, not just skip silently
                if isinstance(e, PermissionError):
                    raise e


def load_prompt(name: str, tasks_file: pathlib.Path | None = None) -> str:
    """Loads a prompt template from the project, global, or built-in hooks.

    Args:
        name: Name of the prompt template (without .md extension).
        tasks_file: Optional path to the tasks file to look for local hooks.

    Returns:
        The content of the prompt template.

    Raises:
        FileNotFoundError: If the prompt template does not exist.
    """
    # 1. Look for local hooks in the project directory
    if tasks_file:
        working_dir = paths.get_working_dir(tasks_file)
        local_hook_path = working_dir / ".lemming" / "hooks" / f"{name}.md"
        if local_hook_path.exists():
            return local_hook_path.read_text(encoding="utf-8")

    # 2. Look in global hooks directory (~/.local/lemming/hooks)
    global_hooks_dir = paths.get_global_hooks_dir()
    global_hook_path = global_hooks_dir / f"{name}.md"
    if global_hook_path.exists():
        content = global_hook_path.read_text(encoding="utf-8")
        if content.strip():
            return content

    # 3. Look in built-in prompts directory (fallback)
    base_path = pathlib.Path(__file__).parent / "prompts"

    # Try exact name first (e.g. taskrunner)
    path = base_path / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")

    # Try hooks subdirectory
    path = base_path / "hooks" / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")

    raise FileNotFoundError(f"Prompt template {name} not found")


def list_hooks(tasks_file: pathlib.Path | None = None) -> list[str]:
    """Lists available orchestrator hooks.

    Args:
        tasks_file: Optional path to the tasks file to look for local hooks.

    Returns:
        A list of hook names.
    """
    hooks = set()

    # 1. Look for local hooks in the project directory
    if tasks_file:
        working_dir = paths.get_working_dir(tasks_file)
        local_hooks_dir = working_dir / ".lemming" / "hooks"
        if local_hooks_dir.exists():
            for f in local_hooks_dir.glob("*.md"):
                hooks.add(f.stem)

    # 2. Look in global hooks directory
    global_hooks_dir = paths.get_global_hooks_dir()
    if global_hooks_dir.exists():
        for f in global_hooks_dir.glob("*.md"):
            hooks.add(f.stem)

    # 3. Look in built-in prompts directory (always included)
    base_path = pathlib.Path(__file__).parent / "prompts" / "hooks"
    if base_path.exists():
        for f in base_path.glob("*.md"):
            hooks.add(f.stem)

    # Ensure 'roadmap' is always last
    hooks_list = sorted(list(hooks))
    if "roadmap" in hooks_list:
        hooks_list.remove("roadmap")
        hooks_list.append("roadmap")

    return hooks_list


def _format_roadmap(data: tasks.Roadmap, current_task_id: str | None = None) -> str:
    """Formats the roadmap for inclusion in prompts.

    Args:
        data: The current Roadmap.
        current_task_id: ID of the task currently being executed, if any.

    Returns:
        A formatted roadmap string.
    """
    roadmap_str = f"## Project Context\n{data.context or 'No context provided.'}\n\n"
    roadmap_str += "## Roadmap\n"

    completed_tasks = [t for t in data.tasks if t.status == tasks.TaskStatus.COMPLETED]

    for i, t in enumerate(data.tasks):
        effective_status = t.requested_status or t.status
        if effective_status == tasks.TaskStatus.COMPLETED:
            marker = "[COMPLETED]"
        elif effective_status == tasks.TaskStatus.FAILED:
            marker = f"[FAILED - {t.attempts}/{data.config.retries} attempt(s)]"
        elif t.status == tasks.TaskStatus.IN_PROGRESS or t.id == current_task_id:
            marker = "[IN PROGRESS]"
        elif t.attempts > 0:
            marker = f"[PENDING - {t.attempts}/{data.config.retries} attempt(s) so far]"
        else:
            marker = "[PENDING]"

        # Bold the current task to make it stand out
        if t.id == current_task_id:
            roadmap_str += f"- **{marker} ({t.id}) {t.description}**\n"
        else:
            roadmap_str += f"- {marker} ({t.id}) {t.description}\n"

        if t.progress:
            # For completed tasks, only show progress for the last 5 to keep the prompt concise
            if effective_status == tasks.TaskStatus.COMPLETED:
                completed_index = completed_tasks.index(t)
                if len(completed_tasks) - completed_index <= 5:
                    for o in t.progress:
                        roadmap_str += f"  - {o}\n"
            else:
                for o in t.progress:
                    roadmap_str += f"  - {o}\n"

    return roadmap_str


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
    from . import runner

    roadmap_str = _format_roadmap(data, current_task_id=finished_task.id)

    # Use requested_status when available — it reflects the actual outcome
    # (e.g. FAILED) while status may still be IN_PROGRESS during hook execution.
    result_status = finished_task.requested_status or finished_task.status

    finished_str = f"Task ID: {finished_task.id}\n"
    finished_str += f"Description: {finished_task.description}\n"
    finished_str += f"Result: {result_status}\n"
    finished_str += f"Attempts: {finished_task.attempts}/{data.config.retries}\n"

    if (
        finished_task.attempts >= data.config.retries
        and result_status == tasks.TaskStatus.FAILED
    ):
        finished_str += "\n!!! WARNING: FINAL ATTEMPT FAILED !!!\n"
        finished_str += (
            f"This task has reached the maximum of {data.config.retries} attempts.\n"
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
        for o in finished_task.progress:
            finished_str += f"- {o}\n"

    # Include the last 100 lines of the runner log for the finished task.
    # We filter out 'Command:' lines because they contain the full previous prompt
    # and cause exponential escaping growth when prompts are re-quoted.
    log_file = paths.get_log_file(tasks_file, finished_task.id)
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                filtered = [line for line in lines if not line.startswith("Command: ")]
                last_lines = [line.rstrip() for line in filtered[-100:]]
                finished_str += "\nExecution log of THIS task (last 100 lines):\n"
                finished_str += "```\n"
                finished_str += "\n".join(last_lines)
                finished_str += "\n```\n"
        except Exception as e:
            finished_str += f"\n(Could not read log file: {e})\n"

    tasks_file_str = runner._pretty_quote(str(tasks_file))
    prompt_template = load_prompt(hook_name, tasks_file)

    return (
        prompt_template.replace("{{roadmap}}", roadmap_str)
        .replace("{{finished_task}}", finished_str)
        .replace("{{finished_task_id}}", finished_task.id)
        .replace("{{tasks_file_name}}", tasks_file.name)
        .replace("{{tasks_file_path}}", tasks_file_str)
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
    from . import runner

    roadmap_str = _format_roadmap(data, current_task_id=task.id)

    # Add parent task context if it's from another project
    if task.parent and task.parent_tasks_file:
        try:
            parent_tasks_path = pathlib.Path(task.parent_tasks_file)
            if parent_tasks_path.exists():
                parent_roadmap = tasks.load_tasks(parent_tasks_path)
                parent_task = next(
                    (t for t in parent_roadmap.tasks if t.id == task.parent), None
                )
                if parent_task:
                    roadmap_str += "\n## Parent Task Context (From root project)\n"
                    roadmap_str += f"- [ ] {parent_task.description}\n"
                    if parent_task.progress:
                        for progress_item in parent_task.progress:
                            roadmap_str += f"  - {progress_item}\n"
        except Exception:
            pass

    progress_str = ""
    if task.progress:
        progress_str = "### Progress from Previous Attempts on THIS Task\n"
        for progress_item in task.progress:
            progress_str += f"- {progress_item}\n"
        progress_str += "\n"

    time_limit_section = ""
    if time_limit > 0:
        time_limit_section = (
            f"\n## Time Limit\n\n"
            f"You have a hard time limit of **{time_limit} minutes**. If you exceed it, your\n"
            f"process will be killed and any unrecorded progress will be lost.\n\n"
            f"- **Record progress early and often.** Don't wait until the end. If you\n"
            f"  are killed, your recorded progress will be passed to the next attempt.\n"
            f"- **If the work is too large** for {time_limit} minutes, break it into smaller\n"
            f"  sub-tasks using `lemming` and complete what you can.\n"
            f"- **Leverage background tasks and subagents** if your runner supports\n"
            f"  them. Long-running operations (builds, test suites, large refactors)\n"
            f"  are good candidates for parallel execution."
        )

    tasks_file_str = runner._pretty_quote(str(tasks_file))
    prompt_template = load_prompt("taskrunner", tasks_file)
    return (
        prompt_template.replace("{{roadmap}}", roadmap_str)
        .replace("{{progress}}", progress_str)
        .replace("{{description}}", task.description)
        .replace("{{tasks_file_name}}", tasks_file.name)
        .replace("{{tasks_file_path}}", tasks_file_str)
        .replace("{{task_id}}", task.id)
        .replace("{{time_limit_section}}", time_limit_section)
    )
