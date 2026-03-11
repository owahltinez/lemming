import os
import pathlib
import shlex
import subprocess
import time

import click
from .core import (
    get_default_tasks_file,
    generate_task_id,
    load_tasks,
    save_tasks,
    get_pending_task,
    mark_task_in_progress,
    update_heartbeat,
    lock_tasks,
)


@click.group()
@click.option(
    "--tasks-file",
    type=click.Path(path_type=pathlib.Path),
    help="Path to the tasks file (defaults to ./tasks.yml or ~/.local/lemming/tasks.yml).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show verbose output.",
)
@click.pass_context
def cli(ctx: click.Context, tasks_file: pathlib.Path | None, verbose: bool):
    """Lemming: An autonomous, iterative task runner for AI agents.

    Lemming orchestrates AI coding agents by walking through a structured `tasks.yml` file.
    It manages the context, tracks task attempts, and records technical lessons learned.
    """
    ctx.ensure_object(dict)
    if tasks_file is None:
        tasks_file = get_default_tasks_file()
    ctx.obj["TASKS_FILE"] = tasks_file.resolve()
    ctx.obj["VERBOSE"] = verbose


@cli.command(short_help="<description> Add a new task to the queue")
@click.argument("description")
@click.option(
    "--index",
    default=-1,
    help="Index to insert the task at (defaults to -1, the end).",
)
@click.option(
    "--agent",
    help="Custom agent to use for this task (overrides the default run agent).",
)
@click.pass_context
def add(ctx: click.Context, description: str, index: int, agent: str | None):
    """Add a new task to the queue."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        task_id = generate_task_id()
        existing_ids = {t["id"] for t in data["tasks"]}
        while task_id in existing_ids:
            task_id = generate_task_id()

        new_task = {
            "id": task_id,
            "description": description,
            "status": "pending",
            "attempts": 0,
            "lessons": [],
        }
        if agent:
            new_task["agent"] = agent

        if index == -1:
            data["tasks"].append(new_task)
        else:
            data["tasks"].insert(index, new_task)

        save_tasks(tasks_file, data)
    
    if verbose:
        click.echo(f"Added task {task_id}: {description}")
    else:
        click.echo(task_id)


@cli.command(short_help="<taskid> Edit an existing task's details")
@click.argument("task_id")
@click.option("--description", help="New description for the task.")
@click.option("--agent", help="New custom agent for the task.")
@click.option("--index", type=int, help="New index in the task queue.")
@click.pass_context
def edit(
    ctx: click.Context,
    task_id: str,
    description: str | None,
    agent: str | None,
    index: int | None,
):
    """Edit an existing task's details."""
    verbose = ctx.obj["VERBOSE"]
    if description is None and agent is None and index is None:
        click.echo(
            "Error: At least one of --description, --agent, or --index must be provided."
        )
        ctx.exit(1)

    tasks_file = ctx.obj["TASKS_FILE"]
    
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        # Find the task
        task_idx = -1
        target_task = None
        for i, t in enumerate(data["tasks"]):
            if t["id"].startswith(task_id):
                task_idx = i
                target_task = t
                break

        if target_task is None:
            click.echo(f"Error: Task {task_id} not found.")
            ctx.exit(1)

        # Apply changes
        if description is not None:
            target_task["description"] = description
        if agent is not None:
            target_task["agent"] = agent

        if index is not None:
            # Move the task to the new index
            task_to_move = data["tasks"].pop(task_idx)
            if index == -1:
                data["tasks"].append(task_to_move)
            else:
                data["tasks"].insert(index, task_to_move)

        save_tasks(tasks_file, data)
    click.echo(f"Task {target_task['id']} updated.")


@cli.command(name="delete", short_help="<taskid> Delete a task from the queue")
@click.argument("task_id")
@click.pass_context
def delete_task(ctx: click.Context, task_id: str):
    """Delete a task from the queue."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        initial_count = len(data["tasks"])
        data["tasks"] = [t for t in data["tasks"] if not t["id"].startswith(task_id)]

        if len(data["tasks"]) < initial_count:
            save_tasks(tasks_file, data)
            click.echo(f"Removed task(s) matching {task_id}")
        else:
            click.echo(f"Error: Task {task_id} not found.")


@cli.command(short_help="<taskid> Show context and task details")
@click.argument("task_id", required=False)
@click.pass_context
def status(ctx: click.Context, task_id: str | None):
    """Show context or task details."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    data = load_tasks(tasks_file)

    if not task_id:
        if verbose:
            click.secho("=== Project Context ===", fg="cyan", bold=True)
            click.echo(data.get("context") or "No context set.")
            click.secho("\n=== Tasks ===", fg="cyan", bold=True)
        
        if not data.get("tasks"):
            if verbose:
                click.echo("No tasks found.")
            return

        for t in data.get("tasks", []):
            if not verbose and t["status"] == "completed":
                continue
            
            if t["status"] == "completed":
                marker = "[x]"
                status_color = "green"
            elif t["status"] == "in_progress":
                marker = "[*]"
                status_color = "cyan"
            else:
                marker = "[ ]"
                status_color = "yellow"
                
            click.secho(f"{marker} ", fg=status_color, nl=False)
            click.echo(f"({t['id']}) {t['description']}")
        
        if not verbose:
            completed_count = sum(1 for t in data["tasks"] if t["status"] == "completed")
            if completed_count > 0:
                click.echo(f"({completed_count} completed tasks hidden)")
        return

    target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)

    if not target:
        click.echo(f"Error: Task {task_id} not found.")
        return

    click.secho(f"Task ID:     {target['id']}", bold=True)
    click.echo(f"Status:      {target['status']}")
    click.echo(f"Description: {target['description']}")
    if target.get("agent"):
        click.echo(f"Custom Agent: {target['agent']}")
    click.echo(f"Attempts:    {target['attempts']}")

    if target.get("lessons"):
        click.secho("\n--- Lessons Learned ---", fg="magenta", bold=True)
        for lesson in target["lessons"]:
            click.echo(f"- {lesson}")


@cli.command(short_help="[<text>] View or set the project context")
@click.argument("context_text", required=False)
@click.option(
    "--file",
    "-f",
    type=click.Path(exists=True, path_type=pathlib.Path),
    help="Read context from a file.",
)
@click.pass_context
def context(ctx: click.Context, context_text: str | None, file: pathlib.Path | None):
    """View or set the project context."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    
    with lock_tasks(tasks_file):
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


@cli.command(short_help="Clear the project context or task queue")
@click.option("--all", is_flag=True, help="Clear both context and tasks.")
@click.option("--tasks", is_flag=True, help="Clear tasks only.")
@click.option("--context", "clear_context", is_flag=True, help="Clear context only.")
@click.pass_context
def clear(ctx: click.Context, all: bool, tasks: bool, clear_context: bool):
    """Clear the project context or task queue."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        do_clear_tasks = tasks
        do_clear_context = clear_context

        if all:
            do_clear_tasks = True
            do_clear_context = True
        elif not tasks and not clear_context:
            do_clear_tasks = True

        if do_clear_tasks:
            data["tasks"] = []
        if do_clear_context:
            data["context"] = ""

        save_tasks(tasks_file, data)

    if do_clear_tasks and do_clear_context:
        click.echo("Cleared all context and tasks.")
    elif do_clear_tasks:
        click.echo("Cleared task queue.")
    elif do_clear_context:
        click.echo("Cleared project context.")


@cli.command(short_help="<taskid> Mark a task as completed and save an outcome")
@click.argument("task_id")
@click.option("--outcome", help="A short summary of the work completed.")
@click.pass_context
def complete(ctx: click.Context, task_id: str, outcome: str | None):
    """Mark a task as completed."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)
        if not target:
            click.echo(f"Error: Task {task_id} not found.")
            ctx.exit(1)

        target["status"] = "completed"
        if outcome:
            if "lessons" not in target:
                target["lessons"] = []
            target["lessons"].append(outcome)

        save_tasks(tasks_file, data)
    click.echo(f"Task {target['id']} marked as completed.")


@cli.command(short_help="<taskid> Mark a completed task as pending")
@click.argument("task_id")
@click.pass_context
def uncomplete(ctx: click.Context, task_id: str):
    """Mark a completed task as pending."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)
        if not target:
            click.echo(f"Error: Task {task_id} not found.")
            ctx.exit(1)

        target["status"] = "pending"
        save_tasks(tasks_file, data)
    click.echo(f"Task {target['id']} marked as pending.")


@cli.command(short_help="<taskid> Record a task failure and save a lesson")
@click.argument("task_id")
@click.option("--lesson", required=True, help="Technical explanation of the failure.")
@click.pass_context
def fail(ctx: click.Context, task_id: str, lesson: str):
    """Record a task failure and save a lesson."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)
        if not target:
            click.echo(f"Error: Task {task_id} not found.")
            ctx.exit(1)

        if "lessons" not in target:
            target["lessons"] = []
        target["lessons"].append(lesson)
        target["status"] = "pending"
        save_tasks(tasks_file, data)
    click.echo(f"Failure recorded for task {target['id']}. Lesson saved.")


@cli.command(short_help="<taskid> Clear a task's attempts and lessons")
@click.argument("task_id")
@click.pass_context
def reset(ctx: click.Context, task_id: str):
    """Clear a task's attempts and lessons."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]
    
    with lock_tasks(tasks_file):
        data = load_tasks(tasks_file)

        target = next((t for t in data["tasks"] if t["id"].startswith(task_id)), None)
        if not target:
            click.echo(f"Error: Task {task_id} not found.")
            ctx.exit(1)

        target["status"] = "pending"
        target["attempts"] = 0
        target["lessons"] = []
        save_tasks(tasks_file, data)
    click.echo(f"Task {target['id']} attempts and lessons cleared.")


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
            if not verbose:
                cmd.append("--quiet")
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


def run_agent_with_heartbeat(
    cmd: list[str], tasks_file: pathlib.Path, task_id: str, verbose: bool
) -> tuple[int, str, str]:
    """Runs the agent process and updates the task heartbeat periodically."""
    process = subprocess.Popen(
        cmd,
        env=os.environ,
        stdout=None if verbose else subprocess.PIPE,
        stderr=None if verbose else subprocess.PIPE,
        text=True,
    )

    while process.poll() is None:
        update_heartbeat(tasks_file, task_id)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            continue

    stdout, stderr = "", ""
    if not verbose:
        stdout, stderr = process.communicate()

    return process.returncode, stdout, stderr


@cli.command(context_settings=dict(ignore_unknown_options=True), short_help="Run the autonomous task execution loop")
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
    """Run the autonomous task execution loop."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]

    while True:
        data = load_tasks(tasks_file)
        current_task = get_pending_task(data)

        if not current_task:
            click.echo("All tasks completed!")
            break

        task_id = current_task["id"]
        
        # Add a small random jitter to avoid race conditions between multiple instances
        import random
        time.sleep(random.uniform(0.1, 0.5))
        
        # Try to claim the task
        if not mark_task_in_progress(tasks_file, task_id, pid=os.getpid()):
            if verbose:
                click.echo(f"Task {task_id} already claimed by another instance. Skipping.")
            continue

        # Re-load data after claiming and increment attempts under lock
        with lock_tasks(tasks_file):
            data = load_tasks(tasks_file)
            current_task = next(t for t in data["tasks"] if t["id"] == task_id)

            current_task["attempts"] += 1
            save_tasks(tasks_file, data)  # Save the incremented attempt right away

        if current_task["attempts"] > max_attempts:
            click.echo(
                f"\nTask {task_id} failed after {max_attempts} attempts. Aborting run."
            )
            click.echo(
                "Please review the code, examine `lemming status <id>`, and adjust the roadmap."
            )
            break

        if verbose:
            click.echo(
                f"\n--- Task {task_id} (Attempt {current_task['attempts']}/{max_attempts}) ---"
            )
            click.echo(f"Working on: {current_task['description']}")
        else:
            click.echo(f"[{task_id}] Attempt {current_task['attempts']}/{max_attempts}: {current_task['description']}")

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
            for i, t in enumerate(completed_tasks):
                roadmap_str += f"- [x] {t['description']}\n"
                if t.get("lessons"):
                    # Only show lessons for the last 5 completed tasks to keep the prompt concise
                    if len(completed_tasks) - i <= 5:
                        for lesson in t["lessons"]:
                            roadmap_str += f"  - {lesson}\n"
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

        tasks_file_str = shlex.quote(str(tasks_file))
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
            f"   `lemming --tasks-file {tasks_file_str} complete {task_id} --outcome \"<brief summary of your work>\"`\n"
            "4. **Failure/Blocker:** If you hit a technical roadblock, cannot fix a bug, or are unable to complete the task, you MUST run this shell command to report failure and save your findings for the next iteration:\n"
            f'   `lemming --tasks-file {tasks_file_str} fail {task_id} --lesson "<Brief technical explanation of what went wrong>"`\n'

            "5. Stop and exit after running either the complete or fail command."
        )

        if verbose:
            click.secho("\n=== Agent Prompt ===", fg="blue", bold=True)
            click.echo(prompt)
            click.secho("====================\n", fg="blue", bold=True)

        cmd = build_agent_command(
            current_task.get("agent") or agent,
            prompt,
            yolo,
            prompt_flag,
            agent_args,
            no_defaults,
            verbose=verbose,
        )

        returncode = 0
        stdout, stderr = "", ""
        try:
            returncode, stdout, stderr = run_agent_with_heartbeat(
                cmd, tasks_file, task_id, verbose
            )
            if returncode != 0:
                if not verbose:
                    if stdout:
                        click.echo(stdout, err=True)
                    if stderr:
                        click.echo(stderr, err=True)
                click.echo(
                    f"\n{agent.capitalize()} execution failed with exit code {returncode}"
                )
                if returncode == 127:
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
        with lock_tasks(tasks_file):
            post_data = load_tasks(tasks_file)
            post_task = next((t for t in post_data["tasks"] if t["id"] == task_id), None)

            if not post_task:
                click.echo("Error: Task disappeared from roadmap during execution.")
                break

            if post_task["status"] == "in_progress":
                # Reset to pending if it's still in_progress but the process finished
                post_task["status"] = "pending"
                save_tasks(tasks_file, post_data)

        if post_task["status"] == "completed":
            if verbose:
                click.echo("Agent successfully reported task completion.")
            else:
                click.echo(f"[{task_id}] Task completed successfully!")
        else:
            if not verbose:
                if stdout:
                    click.echo(stdout)
                if stderr:
                    click.echo(stderr, err=True)
            if verbose:
                click.echo(
                    "Agent finished execution but did NOT report completion. Retrying..."
                )
            if current_task["attempts"] < max_attempts and retry_delay > 0:
                if verbose:
                    click.echo(
                        f"Waiting {retry_delay} seconds before next attempt to avoid rate limits..."
                    )
                time.sleep(retry_delay)


@cli.command(short_help="Launch the web interface")
@click.option("--port", default=8000, help="Port to run the server on.")
@click.option("--host", default="127.0.0.1", help="Host to bind the server to.")
@click.pass_context
def serve(ctx: click.Context, port: int, host: str):
    """Launch the web interface."""
    import uvicorn
    from .api import app
    
    # We pass the TASKS_FILE from context to the API
    # Since the API is already initialized, we might need a way to set it
    # For now, we'll assume the default or let it be handled by env vars if needed
    # But actually, api.py already calls get_default_tasks_file()
    
    click.echo(f"Launching Lemming UI at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
