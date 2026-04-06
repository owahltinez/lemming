import pathlib

import click

from . import prompts
from . import runner
from . import tasks


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
) -> None:
    """Discovers and executes orchestrator hooks for a finished task.

    Args:
        hooks: Explicit list of hooks to run. If None, uses config.hooks.
        final_status: If provided, mark the task with this status after hooks.
    """
    data = tasks.load_tasks(tasks_file)
    task = next((t for t in data.tasks if t.id == task_id), None)
    if not task:
        return

    # Use provided hooks or fall back to configuration
    active_hooks = hooks if hooks is not None else data.config.hooks
    if active_hooks is None:
        active_hooks = prompts.list_hooks(tasks_file)

    if final_status == tasks.TaskStatus.FAILED:
        if "roadmap" in active_hooks:
            active_hooks = ["roadmap"]
        else:
            active_hooks = []

    if not active_hooks:
        if final_status:
            tasks.update_task(tasks_file, task_id, status=final_status, force=True)
        return

    for hook_name in active_hooks:
        # Reload tasks every time to ensure each hook sees progress from previous hooks
        data = tasks.load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if not task:
            if verbose:
                click.echo(f"Task {task_id} not found during hook '{hook_name}' run.")
            continue

        try:
            prompt = prompts.prepare_hook_prompt(hook_name, data, task, tasks_file)
        except FileNotFoundError:
            if verbose:
                click.echo(f"Hook '{hook_name}' prompt not found, skipping.")
            continue

        if verbose:
            click.secho(f"\n=== Hook: {hook_name} Prompt ===", fg="magenta", bold=True)
            click.echo(prompt)
            click.secho("========================\n", fg="magenta", bold=True)

        cmd = runner.build_runner_command(
            runner_name,
            prompt,
            yolo,
            runner_args,
            no_defaults,
            verbose=verbose,
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
            )
            if verbose:
                if returncode != 0:
                    click.echo(f"Hook '{hook_name}' exited with code {returncode}.")
        except Exception as e:
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
                return
            tasks.update_task(tasks_file, task_id, status=final_status, force=True)
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
            import traceback

            click.echo(f"Error finalizing task {task_id}: {e}")
            traceback.print_exc()
