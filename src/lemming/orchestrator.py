import os
import pathlib
import random
import time

import click

from . import prompts
from . import runner
from . import tasks
from .hooks import run_hooks


def run_loop(
    tasks_file: pathlib.Path,
    verbose: bool,
    retry_delay: int,
    yolo: bool,
    no_defaults: bool,
    runner_args: tuple,
    working_dir: pathlib.Path | None = None,
) -> None:
    """Starts the orchestrator loop to autonomously execute pending tasks."""
    while True:
        returncode = 0

        # Reload configuration on each iteration to respond to changes (e.g., from Web UI)
        data = tasks.load_tasks(tasks_file)
        retries = data.config.retries
        runner_name = data.config.runner
        active_hooks = data.config.hooks
        if active_hooks is None:
            active_hooks = prompts.list_hooks(tasks_file)

        current_task = tasks.get_pending_task(data)

        if not current_task:
            click.echo("All tasks completed!")
            break

        task_id = current_task.id

        if current_task.attempts >= retries:
            # Run hooks (like roadmap revision) even on final failure to give them
            # a chance to heal the task before we abort.
            # But do NOT run them if the task was cancelled.
            if returncode != -15:
                # Mark as in_progress so hooks can run and heartbeats work.
                # We use update_task to set requested_status=FAILED so it shows
                # as "Finalizing" in the UI.
                tasks.mark_task_in_progress(tasks_file, task_id)
                tasks.update_task(tasks_file, task_id, status=tasks.TaskStatus.FAILED)

                run_hooks(
                    tasks_file,
                    task_id,
                    runner_name,
                    yolo,
                    runner_args,
                    no_defaults,
                    verbose,
                    hooks=active_hooks,
                    working_dir=working_dir,
                    final_status=tasks.TaskStatus.FAILED,
                )
            else:
                if verbose:
                    click.echo("Task was cancelled. Skipping final failure hooks.")

            # Re-check: if a hook reset/edited/replaced the task, continue the loop
            data = tasks.load_tasks(tasks_file)
            healed_task = next((t for t in data.tasks if t.id == task_id), None)
            if healed_task and healed_task.attempts >= retries:
                click.echo(
                    f"\nTask {task_id} failed after {retries} attempts. Aborting run."
                )
                break

            # Orchestrator healed it (reset attempts, deleted it, etc.) — continue the loop
            click.echo(f"Orchestrator intervened on task {task_id}. Continuing...")
            continue

        # Add a small random jitter to avoid race conditions between multiple instances
        time.sleep(random.uniform(0.1, 0.5))

        # Try to claim the task
        current_task = tasks.claim_task(tasks_file, task_id, pid=os.getpid())
        if not current_task:
            if verbose:
                click.echo(
                    f"Task {task_id} already claimed by another instance. Skipping."
                )
            continue

        if verbose:
            click.echo(
                f"\n--- Task {task_id} (Attempt {current_task.attempts}/{retries}) ---"
            )
            click.echo(f"Working on: {current_task.description}")
        else:
            click.echo(
                f"[{task_id}] Attempt {current_task.attempts}/{retries}: {current_task.description}"
            )

        prompt = prompts.prepare_prompt(data, current_task, tasks_file)

        if verbose:
            click.secho("\n=== Runner Prompt ===", fg="blue", bold=True)
            click.echo(prompt)
            click.secho("====================\n", fg="blue", bold=True)

        cmd = runner.build_runner_command(
            current_task.runner or runner_name,
            prompt,
            yolo,
            runner_args,
            no_defaults,
            verbose=verbose,
        )

        returncode = 0
        stdout, stderr = "", ""
        try:
            returncode, stdout, stderr = runner.run_with_heartbeat(
                cmd,
                tasks_file,
                task_id,
                verbose,
                echo_fn=lambda line: click.echo(line, nl=False),
                cwd=working_dir,
            )
            if returncode != 0:
                click.echo(
                    f"\n{runner_name.capitalize()} execution failed with exit code {returncode}"
                )
                if returncode == 127:
                    click.echo(
                        f"\nNOTE: Command '{runner_name}' not found.\n"
                        "If you are using a shell alias, Python subprocesses cannot see it.\n"
                        "Fixes:\n"
                        f"1. Use the absolute path: `lemming config set runner /path/to/{runner_name}`\n"
                        f"2. Create an executable wrapper script for '{runner_name}' in your PATH."
                    )
        except Exception as e:
            click.echo(f"\nAn error occurred while executing {runner_name}: {e}")

        # Post-execution validation and heartbeat cleanup
        # This will mark the task as COMPLETED or PENDING based on whether
        # the agent called 'lemming complete'.
        post_task = tasks.finish_task_attempt(tasks_file, task_id)

        if not post_task:
            click.echo("Error: Task disappeared from roadmap during execution.")
            break

        # Run orchestrator hooks synchronously if the task requested a status
        # change (completion or failure). This ensures the roadmap is updated
        # and all validation hooks complete before the next task is picked up.
        if post_task.requested_status:
            run_hooks(
                tasks_file,
                task_id,
                runner_name,
                yolo,
                runner_args,
                no_defaults,
                verbose,
                hooks=active_hooks,
                working_dir=working_dir,
                final_status=post_task.requested_status,
            )

        if post_task.status == tasks.TaskStatus.COMPLETED:
            if verbose:
                click.echo("Runner successfully reported task completion.")
            else:
                click.echo(f"[{task_id}] Task completed successfully!")
        else:
            if not verbose:
                if stdout:
                    click.echo(stdout)
                if stderr:
                    click.echo(stderr, err=True)

            if returncode == -15:
                if verbose:
                    click.echo("Task was cancelled. Stopping orchestrator loop.")
                break

            if verbose:
                click.echo(
                    "Runner finished execution but did NOT report completion. Retrying..."
                )

            # Only sleep if the task is still pending AND it wasn't cancelled (it would have requested a status if not)
            if (
                post_task.status == tasks.TaskStatus.PENDING
                and post_task.attempts < retries
                and retry_delay > 0
                and post_task.requested_status is None
            ):
                if verbose:
                    click.echo(
                        f"Waiting {retry_delay} seconds before next attempt to avoid rate limits..."
                    )
                time.sleep(retry_delay)


def parse_timeout(t_str: str) -> float:
    """Parses a duration string into seconds.

    Args:
        t_str: Duration string (e.g., '8h', '30m', '90s').

    Returns:
        The duration in seconds as a float.
    """
    t_str = t_str.strip()
    if t_str == "0" or t_str.startswith("-"):
        return 0.0

    multiplier = 1.0
    if t_str.endswith("h"):
        multiplier = 3600.0
        t_str = t_str[:-1]
    elif t_str.endswith("m"):
        multiplier = 60.0
        t_str = t_str[:-1]
    elif t_str.endswith("s"):
        t_str = t_str[:-1]

    try:
        return float(t_str) * multiplier
    except ValueError:
        return 0.0
