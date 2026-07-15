"""Orchestrator loop that executes pending tasks via runners and hooks."""

import os
import pathlib
import random
import time
import traceback

import click

from . import prompts, runner, tasks
from .hooks import FAILURE_HOOK_PRIORITY, get_hook_priority, list_hooks


def run_hooks(
    tasks_file: pathlib.Path,
    task_id: str,
    runner_name: str,
    yolo: bool,
    runner_args: tuple,
    no_defaults: bool,
    verbose: bool,
    hooks: list[str] | None = None,
    working_dir: pathlib.Path | None = None,
    final_status: tasks.TaskStatus | None = None,
    time_limit: int = 0,
) -> dict[str, int]:
    """Executes orchestrator hooks for a finished task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task the hooks run against.
        runner_name: Name of the runner CLI used to execute the hooks.
        yolo: Whether to run the runner in unattended (yolo) mode.
        runner_args: Extra arguments forwarded to the runner CLI.
        no_defaults: Whether to skip the runner's default arguments.
        verbose: Whether to echo prompts and hook diagnostics.
        hooks: Explicit list of hooks to run. If None, discovers the active
            hooks from the filesystem (see hooks.resolve_hooks).
        working_dir: Working directory for the hook runner processes.
        final_status: If provided, mark the task with this status after hooks.
        time_limit: Time limit in minutes for each hook run (0 disables it).

    Returns:
        Mapping of each executed hook name to its runner exit code (-1 when
        the runner failed to launch). Skipped hooks are absent.
    """
    exit_codes: dict[str, int] = {}
    data = tasks.load_tasks(tasks_file)
    task = next((t for t in data.tasks if t.id == task_id), None)
    if not task:
        return exit_codes

    # Use provided hooks or discover the active set from the filesystem
    active_hooks = hooks if hooks is not None else list_hooks(tasks_file)

    # On failure, only failure hooks (9x priority prefix) run
    if final_status == tasks.TaskStatus.FAILED:
        active_hooks = [
            h
            for h in active_hooks
            if get_hook_priority(h, tasks_file) >= FAILURE_HOOK_PRIORITY
        ]

    if not active_hooks:
        if final_status:
            tasks.update_task(
                tasks_file, task_id, status=final_status, force=True
            )
        return exit_codes

    for hook_name in active_hooks:
        # Reload tasks every time to ensure each hook sees progress from
        # previous hooks
        data = tasks.load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if not task:
            if verbose:
                click.echo(
                    f"Task {task_id} not found during hook '{hook_name}' run."
                )
            continue

        try:
            prompt = prompts.prepare_hook_prompt(
                hook_name, data, task, tasks_file
            )
        except FileNotFoundError:
            if verbose:
                click.echo(f"Hook '{hook_name}' prompt not found, skipping.")
            continue

        if verbose:
            click.secho(
                f"\n=== Hook: {hook_name} Prompt ===", fg="magenta", bold=True
            )
            click.echo(prompt)
            click.secho("========================\n", fg="magenta", bold=True)

        cmd = runner.build_runner_command(
            runner_name,
            prompt,
            yolo,
            runner_args,
            no_defaults,
            verbose=verbose,
            time_limit=time_limit,
        )

        try:
            # Append hooks to the main runner log for a unified execution trace.
            returncode, stdout, stderr = runner.run_with_heartbeat(
                cmd,
                tasks_file,
                task_id,
                verbose,
                echo_fn=lambda line: click.echo(line, nl=False),
                header=f"Hook: {hook_name}",
                cwd=working_dir,
                time_limit=time_limit,
            )
            exit_codes[hook_name] = returncode
            if verbose:
                if returncode != 0:
                    click.echo(
                        f"Hook '{hook_name}' exited with code {returncode}."
                    )
        except Exception as e:
            exit_codes[hook_name] = -1
            click.echo(f"Hook '{hook_name}' error: {e}")

    # Finally mark the task as completed or failed if requested.
    # But first check whether a hook already changed the task (e.g. the
    # roadmap hook reset a failed task for a new approach).  If the task
    # is no longer IN_PROGRESS, a hook already intervened — skip
    # finalization so we don't overwrite the recovery.
    if final_status:
        try:
            data = tasks.load_tasks(tasks_file)
            current = next((t for t in data.tasks if t.id == task_id), None)
            if current and current.status != tasks.TaskStatus.IN_PROGRESS:
                return exit_codes
            tasks.update_task(
                tasks_file, task_id, status=final_status, force=True
            )
        except tasks.TaskNotFoundError:
            # Task may have been deleted by the orchestrator (e.g. after
            # a failure it decided to take a different approach).  This
            # is expected and not an error worth a traceback.
            click.echo(
                f"Task {task_id} was removed before it could be "
                "finalized — the orchestrator likely restructured "
                "the plan."
            )
        except Exception as e:
            click.echo(f"Error finalizing task {task_id}: {e}")
            traceback.print_exc()

    return exit_codes


def _process_exhausted_retries(
    tasks_file: pathlib.Path,
    task_id: str,
    retries: int,
    runner_name: str,
    yolo: bool,
    runner_args: tuple,
    no_defaults: bool,
    verbose: bool,
    active_hooks: list[str],
    working_dir: pathlib.Path | None,
    time_limit: int,
) -> bool:
    """Handles tasks that have exhausted retries.

    Returns True to abort the run loop, False to continue.
    """
    # Run hooks (like roadmap revision) even on final failure to give
    # them a chance to heal the task before we abort.
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
        time_limit=time_limit,
    )

    # Re-check: if a hook reset/edited/replaced the task, continue the loop
    data = tasks.load_tasks(tasks_file)
    healed_task = next((t for t in data.tasks if t.id == task_id), None)
    if healed_task and healed_task.attempts >= retries:
        click.echo(
            f"\nTask {task_id} failed after {retries} attempts. Aborting run."
        )
        return True

    # Orchestrator healed it (reset attempts, deleted it, etc.) — continue
    # the loop
    click.echo(f"Orchestrator intervened on task {task_id}. Continuing...")
    return False


def _process_finalizing_task(
    tasks_file: pathlib.Path,
    task_id: str,
    requested_status: tasks.TaskStatus,
    runner_name: str,
    yolo: bool,
    runner_args: tuple,
    no_defaults: bool,
    verbose: bool,
    active_hooks: list[str],
    working_dir: pathlib.Path | None,
    time_limit: int,
) -> None:
    """Runs hooks for a task that is in a finalizing state."""
    if verbose:
        click.echo(
            f"Task {task_id} resumed in finalizing state "
            f"({requested_status}). Skipping runner."
        )

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
        final_status=requested_status,
        time_limit=time_limit,
    )


def _handle_runner_exit(
    tasks_file: pathlib.Path,
    task_id: str,
    returncode: int,
    stdout: str,
    stderr: str,
    retries: int,
    retry_delay: int,
    runner_name: str,
    yolo: bool,
    runner_args: tuple,
    no_defaults: bool,
    verbose: bool,
    active_hooks: list[str],
    working_dir: pathlib.Path | None,
    time_limit: int,
) -> bool:
    """Handles the aftermath of a task runner exiting.

    Returns True to abort the run loop, False to continue.
    """
    # Post-execution validation and heartbeat cleanup
    # This will mark the task as COMPLETED or PENDING based on whether
    # the agent called 'lemming complete'.
    post_task = tasks.finish_task_attempt(tasks_file, task_id)

    if not post_task:
        click.echo("Error: Task disappeared from roadmap during execution.")
        return True

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
            time_limit=time_limit,
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
            return True

        if verbose:
            click.echo(
                "Runner finished execution but did NOT report completion. "
                "Retrying..."
            )

        # Only sleep if the task is still pending AND it wasn't cancelled
        # (it would have requested a status if not)
        if (
            post_task.status == tasks.TaskStatus.PENDING
            and post_task.attempts < retries
            and retry_delay > 0
            and post_task.requested_status is None
        ):
            if verbose:
                click.echo(
                    f"Waiting {retry_delay} seconds before next attempt "
                    "to avoid rate limits..."
                )
            time.sleep(retry_delay)

    return False


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

        # Reload configuration on each iteration to respond to changes
        # (e.g., from Web UI)
        data = tasks.load_tasks(tasks_file)

        retries = data.config.retries
        time_limit = data.config.time_limit
        runner_name = data.config.runner
        active_hooks = list_hooks(tasks_file)

        current_task = tasks.get_pending_task(data)

        if not current_task:
            click.echo("All tasks completed!")
            break

        task_id = current_task.id

        if current_task.attempts >= retries:
            should_abort = _process_exhausted_retries(
                tasks_file=tasks_file,
                task_id=task_id,
                retries=retries,
                runner_name=runner_name,
                yolo=yolo,
                runner_args=runner_args,
                no_defaults=no_defaults,
                verbose=verbose,
                active_hooks=active_hooks,
                working_dir=working_dir,
                time_limit=time_limit,
            )
            if should_abort:
                break
            continue

        # Add a small random jitter to avoid race conditions between
        # multiple instances
        time.sleep(random.uniform(0.1, 0.5))

        # Try to claim the task
        current_task = tasks.claim_task(tasks_file, task_id, pid=os.getpid())
        if not current_task:
            if verbose:
                click.echo(
                    f"Task {task_id} already claimed by another instance. "
                    "Skipping."
                )
            continue

        if verbose:
            click.echo(
                f"\n--- Task {task_id} "
                f"(Attempt {current_task.attempts}/{retries}) ---"
            )
            click.echo(f"Working on: {current_task.description}")
        else:
            click.echo(
                f"[{task_id}] Attempt {current_task.attempts}/{retries}: "
                f"{current_task.description}"
            )

        # If the task was picked up in a finalizing state, skip the runner
        # and go straight to hooks.
        if current_task.requested_status:
            _process_finalizing_task(
                tasks_file=tasks_file,
                task_id=task_id,
                requested_status=current_task.requested_status,
                runner_name=runner_name,
                yolo=yolo,
                runner_args=runner_args,
                no_defaults=no_defaults,
                verbose=verbose,
                active_hooks=active_hooks,
                working_dir=working_dir,
                time_limit=time_limit,
            )
            continue

        prompt = prompts.prepare_prompt(
            data, current_task, tasks_file, time_limit
        )

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
            time_limit=time_limit,
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
                header="Task Runner",
                cwd=working_dir,
                time_limit=time_limit,
            )
            if returncode == runner.RETURNCODE_TIMEOUT:
                click.echo(
                    f"\nTask {task_id} killed: "
                    f"time limit of {time_limit}m reached."
                )
            elif returncode != 0:
                click.echo(
                    f"\n{runner_name.capitalize()} execution failed "
                    f"with exit code {returncode}"
                )
                if returncode == 127:
                    click.echo(
                        f"\nNOTE: Command '{runner_name}' not found.\n"
                        "If you are using a shell alias, "
                        "Python subprocesses cannot see it.\n"
                        "Fixes:\n"
                        "1. Use the absolute path: `lemming config set "
                        f"runner /path/to/{runner_name}`\n"
                        "2. Create an executable wrapper script for "
                        f"'{runner_name}' in your PATH."
                    )
        except Exception as e:
            click.echo(
                f"\nAn error occurred while executing {runner_name}: {e}"
            )

        should_abort = _handle_runner_exit(
            tasks_file=tasks_file,
            task_id=task_id,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            retries=retries,
            retry_delay=retry_delay,
            runner_name=runner_name,
            yolo=yolo,
            runner_args=runner_args,
            no_defaults=no_defaults,
            verbose=verbose,
            active_hooks=active_hooks,
            working_dir=working_dir,
            time_limit=time_limit,
        )
        if should_abort:
            break


def format_duration(minutes: int) -> str:
    """Formats a duration in minutes as a human-readable string.

    Args:
        minutes: Duration in minutes.

    Returns:
        A human-readable duration string (e.g., '60m', '2h').
    """
    if minutes <= 0:
        return "none"
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


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
