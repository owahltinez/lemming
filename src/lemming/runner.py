import logging
import os
import pathlib
import shlex
import signal
import subprocess
import threading
import time
from typing import Callable

from . import paths
from . import tasks

logger = logging.getLogger(__name__)


def _pretty_quote(s: str) -> str:
    """Quotes a string for shell execution, preferring readable double quotes if it contains single quotes.

    This function is idempotent for single shell words: if s is already a valid shell-quoted
    single word, it is unquoted before being re-quoted prettily.
    """
    if not s:
        return "''"

    # If it's already a single shell word, try to unquote it first to avoid compounding.
    # We only unquote if shlex.split succeeds and returns exactly one token.
    try:
        parts = shlex.split(s)
        if len(parts) == 1:
            # Check if it was actually quoted or escaped.
            # shlex.split("foo") is "foo", but shlex.split("'foo'") is also "foo".
            # We want to canonicalize any quoted string back to its literal form.
            if parts[0] != s or (s.startswith("'") or s.startswith('"')):
                s = parts[0]
    except Exception:
        pass

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


def _shlex_join_pretty(cmd: list[str], max_len: int = -1) -> str:
    """Joins command arguments into a single string with pretty quoting.

    Args:
        cmd: List of command arguments.
        max_len: If > 0, truncate each quoted argument to this length.
    """
    parts = []
    for arg in cmd:
        quoted = _pretty_quote(arg)
        if max_len > 0 and len(quoted) > max_len:
            parts.append(quoted[:max_len] + "... [truncated]")
        else:
            parts.append(quoted)
    return " ".join(parts)


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
    # For the log file, we truncate long arguments (like the prompt) to avoid
    # unreadable logs and exponential escaping growth when prompts are re-quoted.
    log_command_str = _shlex_join_pretty(cmd, max_len=200)
    command_str = _shlex_join_pretty(cmd)

    with open(log_file, "a", encoding="utf-8") as f:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"\n--- Attempt started at {timestamp} ---\n")
        if header:
            f.write(f"{'=' * 80}\n")
            f.write(f"{header.upper()} started at {timestamp}\n")
            f.write(f"{'=' * 80}\n")
        f.write(f"Command: {log_command_str}\n")
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

    try:
        # Heartbeat and cancellation management
        is_claimed = tasks.update_heartbeat(tasks_file, task_id, pid=process.pid)

        def heartbeat_loop():
            """Updates the task heartbeat while the process is running."""
            while process.poll() is None:
                if not tasks.update_heartbeat(tasks_file, task_id):
                    # Task was cancelled or finished — kill the runner subprocess tree
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
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

            process.wait()
        except BaseException:
            # Kill the runner subprocess tree if we are interrupted
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except OSError:
                try:
                    process.kill()
                except OSError:
                    pass
            raise

        return process.returncode, "".join(full_log), ""
    finally:
        if process.stdout and hasattr(process.stdout, "close"):
            process.stdout.close()
