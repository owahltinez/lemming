import logging
import os
import pathlib
import shlex
import subprocess
import threading
import time
from typing import Callable

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

    return sorted(list(hooks))


def _pretty_quote(s: str) -> str:
    """Quotes a string for shell execution, preferring readable double quotes if it contains single quotes."""
    if not s:
        return "''"

    # If shlex.quote says it doesn't need quotes, return as-is
    if shlex.quote(s) == s:
        return s

    # If it contains single quotes, try to use double quotes for better readability
    if "'" in s:
        # If it has !, double quotes might trigger history expansion in interactive bash
        if "!" in s:
            return shlex.quote(s)

        # We need to escape \, ", $, ` inside double quotes
        escaped = (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
        )
        return f'"{escaped}"'

    # Default to standard shlex.quote
    return shlex.quote(s)


def _shlex_join_pretty(cmd: list[str]) -> str:
    """Joins command arguments into a single string with pretty quoting."""
    return " ".join(_pretty_quote(arg) for arg in cmd)


def build_runner_command(
    runner_name: str,
    prompt: str,
    yolo: bool,
    runner_args: tuple | None = None,
    no_defaults: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Constructs the CLI command for the specified runner.

    Args:
        runner_name: Name or path of the runner executable. May contain a
            ``{{prompt}}`` placeholder; when present, the template is
            shlex-split and the placeholder token is replaced with the
            prompt text.  Default flag injection is skipped in template
            mode.
        prompt: The full prompt text to pass to the runner.
        yolo: Whether to enable auto-approval/YOLO mode.
        runner_args: Extra arguments to pass to the runner.
        no_defaults: If True, do not inject default flags for known runners.
        verbose: If True, enable verbose output for supported runners.

    Returns:
        A list of command-line arguments.
    """
    # Template mode: {{prompt}} in runner_name means the user controls the
    # full command layout.  Split, substitute, and return early.
    if "{{prompt}}" in runner_name:
        parts = shlex.split(runner_name)
        cmd = [p.replace("{{prompt}}", prompt) for p in parts]
        if runner_args:
            cmd.extend(runner_args)
        return cmd

    parts = shlex.split(runner_name)
    cmd = [parts[0]]
    extra_parts = parts[1:]
    prompt_arg = None

    runner_base = os.path.basename(parts[0])

    if not no_defaults:
        if runner_base.startswith("gemini"):
            if yolo:
                cmd.extend(["--yolo", "--no-sandbox"])
            prompt_arg = "--prompt"
        elif runner_base.startswith("aider"):
            if yolo:
                cmd.append("--yes")
            if not verbose:
                cmd.append("--quiet")
            prompt_arg = "--message"
        elif runner_base.startswith("claude"):
            if yolo:
                cmd.append("--dangerously-skip-permissions")
            cmd.extend(["--output-format=stream-json", "--verbose"])
            prompt_arg = "--print"
        elif runner_base.startswith("codex"):
            if yolo:
                cmd.append("--yolo")
            prompt_arg = "--instructions"

    if extra_parts:
        cmd.extend(extra_parts)
    if runner_args:
        cmd.extend(runner_args)

    if prompt_arg:
        cmd.extend([prompt_arg, prompt])
    else:
        cmd.append(prompt)

    return cmd


def run_with_heartbeat(
    cmd: list[str],
    tasks_file: pathlib.Path,
    task_id: str,
    verbose: bool,
    echo_fn: Callable[[str], None] = print,
    cwd: pathlib.Path | None = None,
    header: str | None = None,
) -> tuple[int, str, str]:
    """Runs the runner process and updates the task heartbeat periodically.

    Args:
        cmd: The command to execute as a list of strings.
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task being executed (used for heartbeat and parent env var).
        verbose: If True, echo runner output to the console.
        echo_fn: Function to use for echoing output (defaults to print).
        cwd: Optional working directory for the subprocess.
        header: Optional header to write to the log (e.g. "Orchestrator Hook: roadmap").

    Returns:
        A tuple of (returncode, stdout_log, stderr_log). Note: stderr is currently
        merged into stdout_log.
    """
    log_file = paths.get_log_file(tasks_file, task_id)

    # Use a separator for new attempts or hooks
    command_str = _shlex_join_pretty(cmd)
    with open(log_file, "a", encoding="utf-8") as f:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        if header:
            f.write(f"\n--- {header} started at {timestamp} ---\n")
        else:
            f.write(f"\n--- Attempt started at {timestamp} ---\n")
        f.write(f"Command: {command_str}\n")
        f.flush()

    if verbose:
        echo_fn(f"Executing: {command_str}\n\n")

    # Start the process in a new session so we can kill its entire process tree if needed.
    env = os.environ.copy()
    env["LEMMING_PARENT_TASK_ID"] = task_id
    env["LEMMING_PARENT_TASKS_FILE"] = str(tasks_file.resolve())

    process = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
        start_new_session=True,
        cwd=cwd,
    )

    full_log: list[str] = []

    # Heartbeat and cancellation management
    is_claimed = tasks.update_heartbeat(tasks_file, task_id, pid=process.pid)

    def heartbeat_loop():
        """Updates the task heartbeat while the process is running."""
        while process.poll() is None:
            if not tasks.update_heartbeat(tasks_file, task_id):
                # Task was cancelled or finished — kill the runner subprocess tree
                try:
                    os.killpg(os.getpgid(process.pid), __import__("signal").SIGTERM)
                except OSError:
                    try:
                        process.kill()
                    except OSError:
                        pass
                return
            time.sleep(tasks.STALE_THRESHOLD // 2)

    if is_claimed:
        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()

    # Stream output to log file and optionally to console
    try:
        if process.stdout:
            with open(log_file, "a", encoding="utf-8") as f:
                for line in process.stdout:
                    full_log.append(line)
                    f.write(line)
                    f.flush()
                    if verbose:
                        echo_fn(line)
    except Exception as e:
        error_msg = f"\nError reading runner output: {e}\n"
        full_log.append(error_msg)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(error_msg)

    process.wait()
    return process.returncode, "".join(full_log), ""


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
    roadmap_str = f"## Project Context\n{data.context or 'No context provided.'}\n\n"

    roadmap_str += "## All Tasks\n"
    for t in data.tasks:
        if t.status == tasks.TaskStatus.COMPLETED:
            marker = "[COMPLETED]"
        elif t.status == tasks.TaskStatus.IN_PROGRESS:
            marker = "[IN PROGRESS]"
        elif t.attempts > 0:
            marker = f"[PENDING - {t.attempts} attempt(s) so far]"
        else:
            marker = "[PENDING]"

        roadmap_str += f"- {marker} ({t.id}) {t.description}\n"
        if t.outcomes:
            for o in t.outcomes:
                roadmap_str += f"  - {o}\n"

    finished_str = f"Task ID: {finished_task.id}\n"
    finished_str += f"Description: {finished_task.description}\n"
    finished_str += f"Result: {finished_task.status}\n"
    finished_str += f"Attempts: {finished_task.attempts}\n"
    if finished_task.outcomes:
        finished_str += "Outcomes:\n"
        for o in finished_task.outcomes:
            finished_str += f"- {o}\n"

    # Include the last 100 lines of the runner log for the finished task
    log_file = paths.get_log_file(tasks_file, finished_task.id)
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                log_content = f.read()
                lines = log_content.splitlines()
                last_lines = lines[-100:]
                finished_str += "\nExecution log of THIS task (last 100 lines):\n"
                finished_str += "```\n"
                finished_str += "\n".join(last_lines)
                finished_str += "\n```\n"
        except Exception as e:
            finished_str += f"\n(Could not read log file: {e})\n"

    tasks_file_str = shlex.quote(str(tasks_file))
    prompt_template = load_prompt(hook_name, tasks_file)

    return (
        prompt_template.replace("{{roadmap}}", roadmap_str)
        .replace("{{finished_task}}", finished_str)
        .replace("{{finished_task_id}}", finished_task.id)
        .replace("{{tasks_file_name}}", tasks_file.name)
        .replace("{{tasks_file_path}}", tasks_file_str)
    )


def prepare_prompt(
    data: tasks.Roadmap, task: tasks.Task, tasks_file: pathlib.Path
) -> str:
    """Prepares the runner prompt based on the current roadmap state.

    Args:
        data: The current Roadmap.
        task: The Task being executed.
        tasks_file: Path to the tasks YAML file.

    Returns:
        The fully rendered prompt string.
    """
    completed_tasks = [t for t in data.tasks if t.status == tasks.TaskStatus.COMPLETED]
    future_tasks = [
        t
        for t in data.tasks
        if t.status == tasks.TaskStatus.PENDING and t.id != task.id
    ]

    roadmap_str = f"## Project Context\n{data.context or 'No context provided.'}\n\n"

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
                    roadmap_str += "## Parent Task Context (From root project)\n"
                    roadmap_str += f"- [ ] {parent_task.description}\n"
                    if parent_task.outcomes:
                        for outcome_item in parent_task.outcomes:
                            roadmap_str += f"  - {outcome_item}\n"
                    roadmap_str += "\n"
        except Exception:
            pass

    if completed_tasks:
        roadmap_str += "## Completed Tasks (Historical context)\n"
        for i, t in enumerate(completed_tasks):
            roadmap_str += f"- [x] {t.description}\n"
            if t.outcomes:
                # Only show outcomes for the last 5 completed tasks to keep the prompt concise
                if len(completed_tasks) - i <= 5:
                    for outcome_item in t.outcomes:
                        roadmap_str += f"  - {outcome_item}\n"
        roadmap_str += "\n"

    if future_tasks:
        roadmap_str += "## Future Tasks (For architectural foresight only)\n"
        for t in future_tasks:
            roadmap_str += f"- [ ] {t.description}\n"
        roadmap_str += "\n"

    outcomes_str = ""
    if task.outcomes:
        outcomes_str = "### Outcomes from Previous Attempts on THIS Task\n"
        for outcome_item in task.outcomes:
            outcomes_str += f"- {outcome_item}\n"
        outcomes_str += "\n"

    tasks_file_str = shlex.quote(str(tasks_file))
    prompt_template = load_prompt("taskrunner", tasks_file)
    return (
        prompt_template.replace("{{roadmap}}", roadmap_str)
        .replace("{{outcomes}}", outcomes_str)
        .replace("{{description}}", task.description)
        .replace("{{tasks_file_name}}", tasks_file.name)
        .replace("{{tasks_file_path}}", tasks_file_str)
        .replace("{{task_id}}", task.id)
    )
