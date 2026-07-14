"""Discovery and execution of orchestrator hooks for finished tasks."""

import pathlib
import traceback

import click

from . import prompts, runner, tasks


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
    """Discovers and executes orchestrator hooks for a finished task.

    Args:
        tasks_file: Path to the tasks YAML file.
        task_id: ID of the task the hooks run against.
        runner_name: Name of the runner CLI used to execute the hooks.
        yolo: Whether to run the runner in unattended (yolo) mode.
        runner_args: Extra arguments forwarded to the runner CLI.
        no_defaults: Whether to skip the runner's default arguments.
        verbose: Whether to echo prompts and hook diagnostics.
        hooks: Explicit list of hooks to run. If None, uses config.hooks.
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
