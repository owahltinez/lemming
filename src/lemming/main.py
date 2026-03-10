import hashlib
import os
import pathlib
import shlex
import subprocess
import time

import click
import yaml

DEFAULT_TASKS_FILE = pathlib.Path("tasks.yml")


def _get_task_id(description: str) -> str:
    """Generates a stable short hash based on the task description."""
    return hashlib.md5(description.encode("utf-8")).hexdigest()[:8]


def load_tasks(tasks_file: pathlib.Path) -> dict:
    if not tasks_file.exists():
        return {
            "context": "# Project Context\n\nAdd your guidelines here.",
            "tasks": [],
        }
    with open(tasks_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        if not data:
            data = {}
        # Ensure schema
        if "context" not in data:
            data["context"] = ""
        if "tasks" not in data:
            data["tasks"] = []
        return data


def save_tasks(tasks_file: pathlib.Path, data: dict) -> None:
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    with open(tasks_file, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, width=80)


def get_pending_task(data: dict) -> dict | None:
    for task in data.get("tasks", []):
        if task.get("status") == "pending":
            return task
    return None


@click.group()
@click.option(
    "--tasks-file",
    type=click.Path(path_type=pathlib.Path),
    help="Path to the tasks file (defaults to tasks.yml).",
)
@click.pass_context
def cli(ctx: click.Context, tasks_file: pathlib.Path | None):
    """Lemming: An autonomous, iterative task runner for AI agents.

    Lemming orchestrates AI coding agents by walking through a structured `tasks.yml` file.
    It manages the context, tracks task attempts, and records technical lessons learned.
    """
    ctx.ensure_object(dict)
    if tasks_file is None:
        tasks_file = DEFAULT_TASKS_FILE
    ctx.obj["TASKS_FILE"] = tasks_file


@cli.command()
@click.argument("description")
@click.pass_context
def add(ctx: click.Context, description: str):
    """Add a new pending task to the roadmap."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = load_tasks(tasks_file)

    task_id = _get_task_id(description)

    # Check if exists
    for t in data["tasks"]:
        if t["id"] == task_id:
            click.echo(f"Task already exists: {task_id}")
            return

    data["tasks"].append(
        {
            "id": task_id,
            "description": description,
            "status": "pending",
            "attempts": 0,
            "lessons": [],
        }
    )

    save_tasks(tasks_file, data)
    click.echo(f"Added task {task_id}: {description}")


@cli.command("list")
@click.pass_context
def list_tasks(ctx: click.Context):
    """List all tasks and their current status."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = load_tasks(tasks_file)

    if not data["tasks"]:
        click.echo("No tasks found.")
        return

    for t in data["tasks"]:
        marker = "[x]" if t["status"] == "completed" else "[ ]"
        status_color = "green" if t["status"] == "completed" else "yellow"
        click.secho(f"{marker} ", fg=status_color, nl=False)
        click.echo(f"({t['id']}) {t['description']}")


@cli.command()
@click.argument("task_id")
@click.pass_context
def rm(ctx: click.Context, task_id: str):
    """Remove a task by its ID."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = load_tasks(tasks_file)

    initial_count = len(data["tasks"])
    data["tasks"] = [t for t in data["tasks"] if not t["id"].startswith(task_id)]

    if len(data["tasks"]) < initial_count:
        save_tasks(tasks_file, data)
        click.echo(f"Removed task(s) matching {task_id}")
    else:
        click.echo(f"Error: Task {task_id} not found.")


@cli.command()
@click.argument("task_id", required=False)
@click.pass_context
def info(ctx: click.Context, task_id: str | None):
    """Show project context, or detailed metadata for a specific task."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = load_tasks(tasks_file)

    if not task_id:
        click.secho("=== Project Context ===", fg="cyan", bold=True)
        click.echo(data.get("context") or "No context set.")
        click.secho("\n=== Tasks ===", fg="cyan", bold=True)
        if not data.get("tasks"):
            click.echo("No tasks found.")
        for t in data.get("tasks", []):
            marker = "[x]" if t["status"] == "completed" else "[ ]"
            status_color = "green" if t["status"] == "completed" else "yellow"
            click.secho(f"{marker} ", fg=status_color, nl=False)
            click.echo(f"({t['id']}) {t['description']}")
        return

    target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)

    if not target:
        click.echo(f"Error: Task {task_id} not found.")
        return

    click.secho(f"Task ID:     {target['id']}", bold=True)
    click.echo(f"Status:      {target['status']}")
    click.echo(f"Description: {target['description']}")
    click.echo(f"Attempts:    {target['attempts']}")

    if target.get("lessons"):
        click.secho("\n--- Lessons Learned ---", fg="magenta", bold=True)
        for lesson in target["lessons"]:
            click.echo(f"- {lesson}")


@cli.command()
@click.argument("context_text", required=False)
@click.option(
    "--file",
    "-f",
    type=click.Path(exists=True, path_type=pathlib.Path),
    help="Read context from a file.",
)
@click.pass_context
def context(ctx: click.Context, context_text: str | None, file: pathlib.Path | None):
    """View or set the global context/architectural rules for the project."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = load_tasks(tasks_file)

    if file:
        data["context"] = file.read_text(encoding="utf-8")
        save_tasks(tasks_file, data)
        click.echo("Project context updated.")
    elif context_text:
        data["context"] = context_text
        save_tasks(tasks_file, data)
        click.echo("Project context updated.")
    else:
        click.echo(data.get("context") or "No context set.")


@cli.group()
def task():
    """Commands for agents to report task status."""
    pass


cli.add_command(task)


@task.command()
@click.argument("task_id")
@click.pass_context
def complete(ctx: click.Context, task_id: str):
    """Mark a task as completed successfully."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = load_tasks(tasks_file)

    target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)
    if not target:
        click.echo(f"Error: Task {task_id} not found.")
        ctx.exit(1)

    target["status"] = "completed"
    save_tasks(tasks_file, data)
    click.echo(f"Task {target['id']} marked as completed.")


@task.command()
@click.argument("task_id")
@click.option("--lesson", required=True, help="Technical explanation of the failure.")
@click.pass_context
def fail(ctx: click.Context, task_id: str, lesson: str):
    """Record a task failure and save a technical lesson."""
    tasks_file = ctx.obj["TASKS_FILE"]
    data = load_tasks(tasks_file)

    target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)
    if not target:
        click.echo(f"Error: Task {task_id} not found.")
        ctx.exit(1)

    if "lessons" not in target:
        target["lessons"] = []
    target["lessons"].append(lesson)
    save_tasks(tasks_file, data)
    click.echo(f"Failure recorded for task {target['id']}. Lesson saved.")


def build_agent_command(
    agent_name: str,
    prompt: str,
    yolo: bool,
    prompt_flag: str | None = None,
    agent_args: tuple | None = None,
    no_defaults: bool = False,
) -> list[str]:
    """Constructs the CLI command for the specified agent."""
    cmd = [agent_name]
    default_prompt_flag = None

    agent_base = os.path.basename(agent_name)

    if not no_defaults:
        if agent_base.startswith("gemini"):
            if yolo:
                cmd.extend(["--yolo", "--no-sandbox"])
            default_prompt_flag = "--prompt"
        elif agent_base.startswith("aider"):
            if yolo:
                cmd.append("--yes")
            default_prompt_flag = "--message"
        elif agent_base.startswith("claude"):
            if yolo:
                cmd.append("--auto-approve")
            default_prompt_flag = "--prompt"
        elif agent_base.startswith("codex"):
            if yolo:
                cmd.append("--yolo")
            default_prompt_flag = "--instructions"

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


@cli.command(context_settings=dict(ignore_unknown_options=True))
@click.option(
    "--max-attempts", default=3, help="Maximum number of retries for a single task."
)
@click.option(
    "--retry-delay",
    default=10,
    help="Seconds to wait before retrying a failed task (to handle rate limits).",
)
@click.option(
    "--yolo/--no-yolo", default=True, help="Run the agent in YOLO/auto-approve mode."
)
@click.option(
    "--agent",
    default="gemini",
    help="The underlying CLI agent to use (gemini, aider, claude, codex).",
)
@click.option(
    "--no-defaults",
    is_flag=True,
    help="Do not auto-inject default flags (like --yolo) based on agent name.",
)
@click.option(
    "--prompt-flag",
    default=None,
    help="Flag to precede the prompt (e.g. '--message'). If omitted, uses agent defaults.",
)
@click.argument("agent_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run(
    ctx: click.Context,
    max_attempts: int,
    retry_delay: int,
    yolo: bool,
    agent: str,
    no_defaults: bool,
    prompt_flag: str | None,
    agent_args: tuple,
):
    """Run the loop to complete all tasks sequentially."""
    tasks_file = ctx.obj["TASKS_FILE"]

    while True:
        data = load_tasks(tasks_file)
        current_task = get_pending_task(data)

        if not current_task:
            click.echo("All tasks completed!")
            break

        task_id = current_task["id"]
        current_task["attempts"] += 1
        save_tasks(tasks_file, data)  # Save the incremented attempt right away

        if current_task["attempts"] > max_attempts:
            click.echo(
                f"\nTask {task_id} failed after {max_attempts} attempts. Aborting run."
            )
            click.echo(
                "Please review the code, examine `lemming info <id>`, and adjust the roadmap."
            )
            break

        click.echo(
            f"\n--- Task {task_id} (Attempt {current_task['attempts']}/{max_attempts}) ---"
        )
        click.echo(f"Working on: {current_task['description']}")

        # Build context for prompt
        completed_tasks = [t for t in data["tasks"] if t["status"] == "completed"]
        future_tasks = [
            t for t in data["tasks"] if t["status"] == "pending" and t["id"] != task_id
        ]

        roadmap_str = (
            f"## Project Context\n{data.get('context', 'No context provided.')}\n\n"
        )

        if completed_tasks:
            roadmap_str += "## Completed Tasks (Historical context)\n"
            for t in completed_tasks:
                roadmap_str += f"- [x] {t['description']}\n"
            roadmap_str += "\n"

        if future_tasks:
            roadmap_str += "## Future Tasks (For architectural foresight only)\n"
            for t in future_tasks:
                roadmap_str += f"- [ ] {t['description']}\n"
            roadmap_str += "\n"

        lessons_str = ""
        if current_task.get("lessons"):
            lessons_str = "### Lessons Learned from Previous Attempts on THIS Task\n"
            for lesson in current_task["lessons"]:
                lessons_str += f"- {lesson}\n"
            lessons_str += "\n"

        prompt = (
            f"You are an autonomous AI coding agent managed by the 'Lemming' orchestrator.\n\n"
            f"### The Project Roadmap\n"
            f"{roadmap_str}"
            f"{lessons_str}"
            "### Your Assignment\n"
            f"Your CURRENT, EXCLUSIVE task is: **{current_task['description']}**\n\n"
            "### Critical Directives\n"
            "1. **Execute:** Write the code to fulfill the current task. Run any necessary tests.\n"
            f"2. **DO NOT edit `{tasks_file.name}` directly.** You must use the Lemming CLI API.\n"
            f"3. **Success:** When you have completely finished and verified the task, you MUST run this shell command to report success:\n"
            f"   `lemming task complete {task_id}`\n"
            f"4. **Failure/Blocker:** If you hit a technical roadblock, cannot fix a bug, or are unable to complete the task, you MUST run this shell command to report failure and save your findings for the next iteration:\n"
            f'   `lemming task fail {task_id} --lesson "<Brief technical explanation of what went wrong>"`\n'
            "5. Stop and exit after running either the complete or fail command."
        )

        cmd = build_agent_command(
            agent, prompt, yolo, prompt_flag, agent_args, no_defaults
        )
        cmd_str = shlex.join(cmd)
        user_shell = os.environ.get("SHELL", "/bin/sh")

        try:
            # Standard shell execution. Does not load interactive aliases.
            subprocess.run(
                cmd_str, shell=True, check=True, executable=user_shell, env=os.environ
            )
        except subprocess.CalledProcessError as e:
            click.echo(
                f"\n{agent.capitalize()} execution failed with exit code {e.returncode}"
            )
            if e.returncode == 127:
                click.echo(
                    f"\nNOTE: Command '{agent}' not found.\n"
                    "If you are using a shell alias, Python subprocesses cannot see it.\n"
                    "Fixes:\n"
                    f"1. Use the absolute path: `lemming run --agent /path/to/{agent}`\n"
                    f"2. Create an executable wrapper script for '{agent}' in your PATH."
                )
        except Exception as e:
            click.echo(f"\nAn error occurred while executing {agent}: {e}")

        # Post-execution validation
        post_data = load_tasks(tasks_file)
        post_task = next((t for t in post_data["tasks"] if t["id"] == task_id), None)

        if not post_task:
            click.echo("Error: Task disappeared from roadmap during execution.")
            break

        if post_task["status"] == "completed":
            click.echo("Agent successfully reported task completion.")
        else:
            click.echo(
                "Agent finished execution but did NOT report completion. Retrying..."
            )
            if current_task["attempts"] < max_attempts and retry_delay > 0:
                click.echo(
                    f"Waiting {retry_delay} seconds before next attempt to avoid rate limits..."
                )
                time.sleep(retry_delay)


if __name__ == "__main__":
    cli()
