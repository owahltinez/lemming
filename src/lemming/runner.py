import os
import pathlib
import shlex
import subprocess
import threading
import time
from typing import Callable

from . import paths
from . import tasks


def load_prompt(name: str) -> str:
    """Loads a prompt template from the prompts directory.

    Args:
        name: Name of the prompt template (without .md extension).

    Returns:
        The content of the prompt template.

    Raises:
        FileNotFoundError: If the prompt template does not exist.
    """
    base_path = pathlib.Path(__file__).parent / "prompts"
    prompt_path = base_path / f"{name}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template {name} not found at {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


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
) -> tuple[int, str, str]:
    """Runs the runner process and updates the task heartbeat periodically.

    Args:
        cmd: The command to execute as a list of strings.
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task being executed.
        verbose: If True, echo runner output to the console.
        echo_fn: Function to use for echoing output (defaults to print).

    Returns:
        A tuple of (returncode, stdout_log, stderr_log). Note: stderr is currently
        merged into stdout_log.
    """
    log_file = paths.get_log_file(tasks_file, task_id)

    # Use a separator for new attempts
    command_str = _shlex_join_pretty(cmd)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n--- Attempt started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        f.write(f"Command: {command_str}\n")
        f.flush()

    if verbose:
        echo_fn(f"Executing: {command_str}\n\n")

    # Start the process in a new session so we can kill its entire process tree if needed.
    env = os.environ.copy()
    env["LEMMING_PARENT_TASK_ID"] = task_id

    process = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
        start_new_session=True,
    )

    full_log: list[str] = []

    def heartbeat_loop():
        """Updates the task heartbeat while the process is running."""
        while process.poll() is None:
            tasks.update_heartbeat(tasks_file, task_id)
            time.sleep(tasks.STALE_THRESHOLD // 2)

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


def prepare_review_prompt(
    data: tasks.Roadmap, finished_task: tasks.Task, tasks_file: pathlib.Path
) -> str:
    """Prepares the reviewer prompt after a task finishes.

    Args:
        data: The current Roadmap.
        finished_task: The Task that just finished executing.
        tasks_file: Path to the tasks YAML file.

    Returns:
        The fully rendered reviewer prompt string.
    """
    roadmap_str = f"## Project Context\n{data.context or 'No context provided.'}\n\n"

    roadmap_str += "## All Tasks\n"
    for t in data.tasks:
        if t.status == "completed":
            marker = "[COMPLETED]"
        elif t.status == "in_progress":
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

    tasks_file_str = shlex.quote(str(tasks_file))
    prompt_template = load_prompt("reviewer")
    return (
        prompt_template.replace("{{roadmap}}", roadmap_str)
        .replace("{{finished_task}}", finished_str)
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
    completed_tasks = [t for t in data.tasks if t.status == "completed"]
    future_tasks = [t for t in data.tasks if t.status == "pending" and t.id != task.id]

    roadmap_str = f"## Project Context\n{data.context or 'No context provided.'}\n\n"

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
    prompt_template = load_prompt("taskrunner")
    return (
        prompt_template.replace("{{roadmap}}", roadmap_str)
        .replace("{{outcomes}}", outcomes_str)
        .replace("{{description}}", task.description)
        .replace("{{tasks_file_name}}", tasks_file.name)
        .replace("{{tasks_file_path}}", tasks_file_str)
        .replace("{{task_id}}", task.id)
    )
