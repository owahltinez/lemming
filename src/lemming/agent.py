import os
import pathlib
import shlex
import subprocess
import threading
import time

from . import paths
from . import tasks
from . import utils


def load_prompt(name: str) -> str:
    """Loads a prompt template from the prompts directory."""
    base_path = pathlib.Path(__file__).parent / "prompts"
    prompt_path = base_path / f"{name}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template {name} not found at {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def build_agent_command(
    agent_name: str,
    prompt: str,
    yolo: bool,
    prompt_flag: str | None = None,
    agent_args: tuple | None = None,
    no_defaults: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Constructs the CLI command for the specified agent."""
    parts = shlex.split(agent_name)
    cmd = [parts[0]]
    extra_parts = parts[1:]
    default_prompt_flag = None

    agent_base = os.path.basename(parts[0])

    if not no_defaults:
        if agent_base.startswith("gemini"):
            if yolo:
                cmd.extend(["--yolo", "--no-sandbox"])
            default_prompt_flag = "--prompt"
        elif agent_base.startswith("aider"):
            if yolo:
                cmd.append("--yes")
            if not verbose:
                cmd.append("--quiet")
            default_prompt_flag = "--message"
        elif agent_base.startswith("claude"):
            if yolo:
                cmd.append("--dangerously-skip-permissions")
            cmd.extend(["--output-format=stream-json", "--verbose"])
            default_prompt_flag = "--print"
        elif agent_base.startswith("codex"):
            if yolo:
                cmd.append("--yolo")
            default_prompt_flag = "--instructions"

    if extra_parts:
        cmd.extend(extra_parts)
    if agent_args:
        cmd.extend(agent_args)

    p_flag = prompt_flag if prompt_flag is not None else default_prompt_flag

    if p_flag:
        if not p_flag.startswith("-"):
            p_flag = "--" + p_flag
        cmd.extend([p_flag, prompt])
    else:
        cmd.append(prompt)

    return cmd


def run_agent_with_heartbeat(
    cmd: list[str],
    tasks_file: pathlib.Path,
    task_id: str,
    verbose: bool,
    echo_fn=print,
) -> tuple[int, str, str]:
    """Runs the agent process and updates the task heartbeat periodically."""
    log_file = paths.get_log_file(tasks_file, task_id)

    # Use a separator for new attempts
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n--- Attempt started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        f.flush()

    process = subprocess.Popen(
        cmd,
        env=os.environ,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # Combine stdout and stderr for simple logging
        text=True,
        bufsize=1,  # Line buffered
        errors="replace",  # Robust decoding
    )

    full_log = []

    def heartbeat_loop():
        while process.poll() is None:
            tasks.update_heartbeat(tasks_file, task_id)
            time.sleep(utils.STALE_THRESHOLD // 2)

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    # Stream output to log file and optionally to console
    if process.stdout:
        with open(log_file, "a", encoding="utf-8") as f:
            for line in process.stdout:
                full_log.append(line)
                f.write(line)
                f.flush()
                if verbose:
                    echo_fn(line)

    process.wait()
    return process.returncode, "".join(full_log), ""


def prepare_prompt(data: dict, task: dict, tasks_file: pathlib.Path) -> str:
    """Prepares the agent prompt based on the current state."""
    completed_tasks = [t for t in data["tasks"] if t["status"] == "completed"]
    future_tasks = [
        t for t in data["tasks"] if t["status"] == "pending" and t["id"] != task["id"]
    ]

    roadmap_str = (
        f"## Project Context\n{data.get('context', 'No context provided.')}\n\n"
    )

    if completed_tasks:
        roadmap_str += "## Completed Tasks (Historical context)\n"
        for i, t in enumerate(completed_tasks):
            roadmap_str += f"- [x] {t['description']}\n"
            if t.get("outcomes"):
                # Only show outcomes for the last 5 completed tasks to keep the prompt concise
                if len(completed_tasks) - i <= 5:
                    for outcome_item in t["outcomes"]:
                        roadmap_str += f"  - {outcome_item}\n"
        roadmap_str += "\n"

    if future_tasks:
        roadmap_str += "## Future Tasks (For architectural foresight only)\n"
        for t in future_tasks:
            roadmap_str += f"- [ ] {t['description']}\n"
        roadmap_str += "\n"

    outcomes_str = ""
    if task.get("outcomes"):
        outcomes_str = "### Outcomes from Previous Attempts on THIS Task\n"
        for outcome_item in task["outcomes"]:
            outcomes_str += f"- {outcome_item}\n"
        outcomes_str += "\n"

    tasks_file_str = shlex.quote(str(tasks_file))
    prompt_template = load_prompt("taskrunner")
    return (
        prompt_template.replace("{{roadmap}}", roadmap_str)
        .replace("{{outcomes}}", outcomes_str)
        .replace("{{description}}", task["description"])
        .replace("{{tasks_file_name}}", tasks_file.name)
        .replace("{{tasks_file_path}}", tasks_file_str)
        .replace("{{task_id}}", task["id"])
    )
