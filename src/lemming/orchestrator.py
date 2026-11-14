"""Orchestrator loop that executes pending tasks via runners and hooks."""

import os
import pathlib
import random
import time
import traceback

import click

from . import (
    models,
    paths,
    persistence,
    prompts,
    runner,
    shutdown,
    snapshots,
)
from . import (
    scope as scope_mod,
)
from .hooks import FAILURE_HOOK_PRIORITY, get_hook_priority, select_hooks
from .tasks import lifecycle, limits, operations, progress, queries

# Minutes a single hook may run, so a hung hook cannot burn a task's budget.
HOOK_TIME_LIMIT = 15


def _persist_completed_snapshot(
    tasks_file: pathlib.Path,
    task_id: str,
    working_dir: pathlib.Path,
) -> None:
    """Captures and persists post_tree and diff_summary on a completed task."""
    post_tree = snapshots.snapshot_tree(tasks_file, working_dir)
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        current = next((t for t in data.tasks if t.id == task_id), None)
        if current is None:
            return
        current.post_tree = post_tree
        current.diff_summary = (
            snapshots.summarize_diff(tasks_file, current.pre_tree, post_tree)
            if current.pre_tree and post_tree
            else None
        )
        persistence.save_tasks(tasks_file, data)


def _record_hook_tree_and_read_rejection(
    tasks_file: pathlib.Path,
    task_id: str,
    hook_name: str,
    hook_pre_tree: str | None,
    hook_post_tree: str | None,
) -> str | None:
    """Persists a hook's tree pair (if any) and returns the task's rejection."""
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if task is None:
            return None
        if (
            task.status == models.TaskStatus.IN_PROGRESS
            and hook_pre_tree is not None
            and hook_post_tree is not None
        ):
            if task.hook_trees is None:
                task.hook_trees = {}
            task.hook_trees[hook_name] = [hook_pre_tree, hook_post_tree]
            persistence.save_tasks(tasks_file, data)
        return task.rejection


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
    final_status: models.TaskStatus | None = None,
    time_limit: int = 0,
    model_name: str | None = None,
    scope: str | None = None,
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
        hooks: Explicit list of hooks to run. If None, runs the project's
            enabled hooks, with the task's own reviews replacing the
            project's (see hooks.select_hooks). A oneshot task runs none of
            them on success.
        working_dir: Working directory for the hook runner processes.
        final_status: If provided, mark the task with this status after hooks.
        time_limit: Task time limit in minutes; each hook run gets at most
            HOOK_TIME_LIMIT of it (0 disables it).
        model_name: Model to request from the hook runner, if any.
        scope: What the hooks should look at. Defaults to the work the
            finished task left behind.

    Returns:
        Mapping of each executed hook name to its runner exit code (-1 when
        the runner failed to launch). Skipped hooks are absent.
    """
    exit_codes: dict[str, int] = {}
    working_dir = working_dir or paths.get_working_dir(tasks_file)
    data = persistence.load_tasks(tasks_file)
    task = next((t for t in data.tasks if t.id == task_id), None)
    if not task:
        return exit_codes

    # Use provided hooks or the project's and task's selection
    active_hooks = (
        hooks
        if hooks is not None
        else select_hooks(tasks_file, data.config.hooks, task.hooks)
    )

    # A oneshot task buys its latency back by skipping the post-task
    # review, but a failing one still gets failure-hook recovery below.
    if task.oneshot and final_status == models.TaskStatus.COMPLETED:
        active_hooks = []

    # On failure, only failure hooks (9x priority prefix) run
    if final_status == models.TaskStatus.FAILED:
        active_hooks = [
            h
            for h in active_hooks
            if get_hook_priority(h, tasks_file) >= FAILURE_HOOK_PRIORITY
        ]

    if not active_hooks:
        if final_status:
            operations.update_task(
                tasks_file, task_id, status=final_status, force=True
            )
            if final_status == models.TaskStatus.COMPLETED:
                _persist_completed_snapshot(tasks_file, task_id, working_dir)
        return exit_codes

    hook_limit = min(time_limit, HOOK_TIME_LIMIT) if time_limit else 0
    for hook_index, hook_name in enumerate(active_hooks):
        # Reload tasks every time to ensure each hook sees progress from
        # previous hooks
        data = persistence.load_tasks(tasks_file)
        task = next((t for t in data.tasks if t.id == task_id), None)
        if not task:
            if verbose:
                click.echo(
                    f"Task {task_id} not found during hook '{hook_name}' run."
                )
            continue
        if task.status == models.TaskStatus.CANCELLED or (
            hook_index > 0
            and final_status is not None
            and task.status != models.TaskStatus.IN_PROGRESS
        ):
            break

        try:
            prompt = prompts.prepare_hook_prompt(
                hook_name, data, task, tasks_file, scope=scope
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
            time_limit=hook_limit,
            working_dir=working_dir,
            model=model_name,
        )

        hook_pre_tree = snapshots.snapshot_tree(tasks_file, working_dir)
        hook_pre_head = snapshots.head_commit(working_dir)
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
                time_limit=hook_limit,
            )
            exit_codes[hook_name] = returncode
            if returncode != 0:
                # An exit code alone sends the operator to the log; the hook
                # usually already said what went wrong.
                reason = runner.extract_error_message(stdout)
                if reason:
                    click.echo(f"Hook '{hook_name}' reported: {reason}")
                if verbose:
                    click.echo(
                        f"Hook '{hook_name}' exited with code {returncode}."
                    )
        except persistence.CorruptedTasksError:
            # Not a hook failure: reporting it as one blames the wrong thing
            # and lets the loop carry on writing to an unreadable roadmap.
            if hook_pre_tree is not None:
                snapshots.restore_tree(
                    tasks_file,
                    working_dir,
                    hook_pre_tree,
                    target_head=hook_pre_head,
                )
            raise
        except Exception as e:
            exit_codes[hook_name] = runner.RETURNCODE_LAUNCH_FAILED
            click.echo(f"Hook '{hook_name}' error: {e}")
        except BaseException:
            if hook_pre_tree is not None:
                snapshots.restore_tree(
                    tasks_file,
                    working_dir,
                    hook_pre_tree,
                    target_head=hook_pre_head,
                )
            raise

        hook_post_tree = None
        if exit_codes[hook_name] != 0:
            latest_data = persistence.load_tasks(tasks_file)
            latest_task = next(
                (t for t in latest_data.tasks if t.id == task_id), None
            )
            if (
                latest_task is not None
                and latest_task.status == models.TaskStatus.CANCELLED
            ):
                break
            if hook_pre_tree is not None:
                snapshots.restore_tree(
                    tasks_file,
                    working_dir,
                    hook_pre_tree,
                    target_head=hook_pre_head,
                )
        elif exit_codes[hook_name] == 0 and hook_pre_tree is not None:
            hook_post_tree = snapshots.snapshot_tree(tasks_file, working_dir)

        # A hook that finds a defect it cannot fix rejects the completion
        # rather than failing its own process. Attribute it here, while it is
        # still known which hook ran, but keep going: the later hooks (the
        # roadmap hook above all) need to see the rejection too.
        rejection = _record_hook_tree_and_read_rejection(
            tasks_file, task_id, hook_name, hook_pre_tree, hook_post_tree
        )
        if rejection and rejection != task.rejection:
            # reject_task bounds the reason to one progress entry, but the
            # attribution prefix can still push it over; an oversized entry
            # would raise here and take the whole loop down.
            message = f"Hook '{hook_name}' rejected completion: {rejection}"
            progress.add_progress(
                tasks_file, task_id, message[: limits.MAX_PROGRESS_ENTRY_CHARS]
            )
            click.echo(message)

    # Finally mark the task as completed or failed if requested.
    # But first check whether a hook already changed the task (e.g. the
    # roadmap hook reset a failed task for a new approach).  If the task
    # is no longer IN_PROGRESS, a hook already intervened — skip
    # finalization so we don't overwrite the recovery.
    if final_status:
        try:
            data = persistence.load_tasks(tasks_file)
            current = next((t for t in data.tasks if t.id == task_id), None)
            if current and current.status != models.TaskStatus.IN_PROGRESS:
                return exit_codes

            # If any hook run was killed or crashed (e.g. the model became
            # unavailable mid-finalization), the completion is unverified.
            # Revert the task to pending — keeping its attempt count — so
            # the whole task is retried instead of being silently marked
            # completed. Failure finalization proceeds regardless: those
            # tasks have already exhausted their attempts.
            # A rejection is the same verdict arrived at deliberately: a
            # hook that ran to completion and refused the result.
            failed_hooks = [h for h, code in exit_codes.items() if code != 0]
            rejection = current.rejection if current else None
            if final_status == models.TaskStatus.COMPLETED and (
                failed_hooks or rejection
            ):
                reasons = []
                if failed_hooks:
                    reasons.append(f"hooks failed ({', '.join(failed_hooks)})")
                if rejection:
                    # The reason is already recorded against the hook that
                    # gave it; repeating it here would crowd the handful of
                    # entries the next attempt is shown.
                    reasons.append("was rejected")
                detail = "; ".join(reasons)
                progress.add_progress(
                    tasks_file,
                    task_id,
                    f"Finalization {detail}; task reverted to pending.",
                )
                lifecycle.revert_task_to_pending(tasks_file, task_id)
                click.echo(
                    f"Task {task_id} finalization {detail}. "
                    "Reverting to pending."
                )
                return exit_codes

            operations.update_task(
                tasks_file, task_id, status=final_status, force=True
            )
            if final_status == models.TaskStatus.COMPLETED:
                _persist_completed_snapshot(tasks_file, task_id, working_dir)
        except models.TaskNotFoundError:
            # Task may have been deleted by the orchestrator (e.g. after
            # a failure it decided to take a different approach).  This
            # is expected and not an error worth a traceback.
            click.echo(
                f"Task {task_id} was removed before it could be "
                "finalized — the orchestrator likely restructured "
                "the plan."
            )
        except persistence.CorruptedTasksError:
            # An unreadable roadmap is not a finalization failure to log and
            # move past: let it stop the loop while the file is still intact.
            raise
        except Exception as e:
            click.echo(f"Error finalizing task {task_id}: {e}")
            traceback.print_exc()

    return exit_codes


def _rewind_to_pre_tree(
    tasks_file: pathlib.Path,
    task_id: str,
    working_dir: pathlib.Path,
    patch_name: str = "attempt-final.patch",
) -> None:
    """Saves the attempt's patch, restores pre_tree, and clears trees."""
    data = persistence.load_tasks(tasks_file)
    task = next((t for t in data.tasks if t.id == task_id), None)
    if task and task.pre_tree:
        current_tree = snapshots.snapshot_tree(tasks_file, working_dir)
        if current_tree and current_tree != task.pre_tree:
            lifecycle.write_patch_artifact(
                tasks_file,
                task_id,
                patch_name,
                task.pre_tree,
                current_tree,
            )
        if current_tree != task.pre_tree or (
            task.pre_head is not None
            and snapshots.head_commit(working_dir) != task.pre_head
        ):
            snapshots.restore_tree(
                tasks_file,
                working_dir,
                task.pre_tree,
                current_tree=current_tree,
                target_head=task.pre_head,
            )
    with persistence.lock_tasks(tasks_file):
        latest = persistence.load_tasks(tasks_file)
        target = next((t for t in latest.tasks if t.id == task_id), None)
        if target is not None and (
            target.runner_tree is not None or target.hook_trees is not None
        ):
            target.runner_tree = None
            target.hook_trees = None
            persistence.save_tasks(tasks_file, latest)


def _restore_cancelled_task(
    tasks_file: pathlib.Path,
    task_id: str,
    working_dir: pathlib.Path,
) -> None:
    """Restores workspace and clears trees if a cancelled task has pre_tree."""
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id == task_id), None)
        if target is None:
            return
        target_tree = target.pre_tree
        target_head = target.pre_head
        if target_tree is not None:
            current_tree = snapshots.snapshot_tree(tasks_file, working_dir)
            if current_tree != target_tree or (
                target_head is not None
                and snapshots.head_commit(working_dir) != target_head
            ):
                lifecycle.write_patch_artifact(
                    tasks_file,
                    task_id,
                    "cancelled.patch",
                    target_tree,
                    current_tree,
                )
                snapshots.restore_tree(
                    tasks_file,
                    working_dir,
                    target_tree,
                    current_tree=current_tree,
                    target_head=target_head,
                )
        if (
            target.pre_tree is not None
            or target.pre_head is not None
            or target.runner_tree is not None
            or target.hook_trees is not None
        ):
            target.pre_tree = None
            target.pre_head = None
            target.runner_tree = None
            target.hook_trees = None
            persistence.save_tasks(tasks_file, data)


def recover_abandoned_tasks(
    tasks_file: pathlib.Path,
    working_dir: pathlib.Path | None = None,
    *,
    exiting: bool = False,
) -> None:
    """Rewinds in-progress tasks whose owner died to their last checkpoint.

    The loop that owned them runs this on its way out, and the next loop on
    startup in case the last one was killed before it could. A task with no
    recorded owner was released cleanly and is left alone.

    Args:
        tasks_file: Path to the tasks YAML file.
        working_dir: Workspace to rewind; defaults to the tasks file's.
        exiting: Whether the caller is the owning loop, alive until now
            rather than only until its last heartbeat.
    """
    working_dir = working_dir or paths.get_working_dir(tasks_file)
    for task in persistence.load_tasks(tasks_file).tasks:
        if (
            task.status != models.TaskStatus.IN_PROGRESS
            or task.pid is None
            or (task.pid != os.getpid() and lifecycle.is_pid_alive(task.pid))
        ):
            continue
        click.echo(f"Task {task.id} was cut off; rewinding it to a checkpoint.")
        if task.requested_status != models.TaskStatus.COMPLETED:
            _rewind_to_pre_tree(
                tasks_file,
                task.id,
                working_dir,
                f"attempt-{max(1, task.attempts)}.patch",
            )
        elif task.runner_tree is not None:
            # Completed work survives; only the reviews of it start over.
            current = snapshots.snapshot_tree(tasks_file, working_dir)
            if current is not None and current != task.runner_tree:
                lifecycle.write_patch_artifact(
                    tasks_file,
                    task.id,
                    "interrupted-review.patch",
                    task.runner_tree,
                    current,
                )
                snapshots.restore_tree(
                    tasks_file,
                    working_dir,
                    task.runner_tree,
                    current_tree=current,
                )
        with persistence.lock_tasks(tasks_file):
            latest = persistence.load_tasks(tasks_file)
            target = next((t for t in latest.tasks if t.id == task.id), None)
            if target is None or target.status != models.TaskStatus.IN_PROGRESS:
                continue
            lifecycle.update_run_time(
                target,
                time.time()
                if exiting
                else max(
                    target.last_heartbeat or 0, target.last_started_at or 0
                ),
            )
            # A cut-off attempt is retried uncharged; the next run finalizes
            # any outcome the agent had already requested.
            if target.requested_status is None:
                target.status = models.TaskStatus.PENDING
                target.attempts = max(0, target.attempts - 1)
                target.rejection = None
            target.pid = None
            target.last_heartbeat = None
            target.active_execution_component = None
            target.active_execution_started_at = None
            target.hook_trees = None
            persistence.save_tasks(tasks_file, latest)


def _process_exhausted_retries(
    tasks_file: pathlib.Path,
    task_id: str,
    retries: int,
    runner_name: str,
    yolo: bool,
    runner_args: tuple,
    no_defaults: bool,
    verbose: bool,
    active_hooks: list[str] | None,
    working_dir: pathlib.Path | None,
    time_limit: int,
    model_name: str | None = None,
    scope: str | None = None,
) -> bool:
    """Handles tasks that have exhausted retries.

    Returns True to abort the run loop, False to continue.
    """
    working_dir = working_dir or paths.get_working_dir(tasks_file)
    _rewind_to_pre_tree(tasks_file, task_id, working_dir)

    # Run hooks (like roadmap revision) even on final failure to give
    # them a chance to heal the task before we abort.
    # Mark as in_progress so hooks can run and heartbeats work.
    # We use update_task to set requested_status=FAILED so it shows
    # as "Finalizing" in the UI.
    lifecycle.mark_task_in_progress(tasks_file, task_id)
    operations.update_task(tasks_file, task_id, status=models.TaskStatus.FAILED)

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
        final_status=models.TaskStatus.FAILED,
        time_limit=time_limit,
        model_name=model_name,
        scope=scope,
    )

    # Re-check: if a hook reset/edited/replaced the task, continue the loop
    data = persistence.load_tasks(tasks_file)
    healed_task = next((t for t in data.tasks if t.id == task_id), None)
    if (
        healed_task is not None
        and healed_task.status == models.TaskStatus.PENDING
        and healed_task.pre_tree is None
    ):
        snapshots.prune_snapshots(
            tasks_file, snapshots.collect_task_trees(data.tasks)
        )
    if (
        healed_task
        and models.TaskStatus.FAILED
        in (
            healed_task.status,
            healed_task.requested_status,
        )
        and healed_task.attempts >= retries
    ):
        click.echo(
            f"\nTask {task_id} failed after {retries} attempts. Aborting run."
        )
        return True

    # Orchestrator healed or superseded it — continue the loop.
    click.echo(f"Orchestrator intervened on task {task_id}. Continuing...")
    return False


def _process_finalizing_task(
    tasks_file: pathlib.Path,
    task_id: str,
    requested_status: models.TaskStatus,
    runner_name: str,
    yolo: bool,
    runner_args: tuple,
    no_defaults: bool,
    verbose: bool,
    active_hooks: list[str] | None,
    working_dir: pathlib.Path | None,
    time_limit: int,
    model_name: str | None = None,
    scope: str | None = None,
) -> None:
    """Runs hooks for a task that is in a finalizing state."""
    if verbose:
        click.echo(
            f"Task {task_id} resumed in finalizing state "
            f"({requested_status}). Skipping runner."
        )

    resolved_dir = working_dir or paths.get_working_dir(tasks_file)
    data = persistence.load_tasks(tasks_file)
    current_task = next((t for t in data.tasks if t.id == task_id), None)
    hook_scope = scope
    if current_task is not None:
        if requested_status != models.TaskStatus.COMPLETED:
            if current_task.pre_tree:
                current_tree = snapshots.snapshot_tree(tasks_file, resolved_dir)
                if current_tree and current_tree != current_task.pre_tree:
                    lifecycle.write_patch_artifact(
                        tasks_file,
                        task_id,
                        f"attempt-{max(1, current_task.attempts)}.patch",
                        current_task.pre_tree,
                        current_tree,
                    )
                if current_tree != current_task.pre_tree or (
                    current_task.pre_head is not None
                    and snapshots.head_commit(resolved_dir)
                    != current_task.pre_head
                ):
                    snapshots.restore_tree(
                        tasks_file,
                        resolved_dir,
                        current_task.pre_tree,
                        current_tree=current_tree,
                        target_head=current_task.pre_head,
                    )
            if (
                current_task.runner_tree is not None
                or current_task.hook_trees is not None
            ):
                with persistence.lock_tasks(tasks_file):
                    latest = persistence.load_tasks(tasks_file)
                    target = next(
                        (t for t in latest.tasks if t.id == task_id), None
                    )
                    if target is not None:
                        target.runner_tree = None
                        target.hook_trees = None
                        persistence.save_tasks(tasks_file, latest)
        elif requested_status == models.TaskStatus.COMPLETED:
            if current_task.runner_tree is None:
                rt = snapshots.snapshot_tree(tasks_file, resolved_dir)
                if rt is not None:
                    with persistence.lock_tasks(tasks_file):
                        latest = persistence.load_tasks(tasks_file)
                        target = next(
                            (t for t in latest.tasks if t.id == task_id), None
                        )
                        if target is not None:
                            target.runner_tree = rt
                            persistence.save_tasks(tasks_file, latest)
                    current_task.runner_tree = rt
            if (
                hook_scope is None
                and current_task.pre_tree
                and current_task.runner_tree
            ):
                changed = snapshots.changed_files(
                    tasks_file, current_task.pre_tree, current_task.runner_tree
                )
                hook_scope = scope_mod.describe(changed, resolved_dir)

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
        model_name=model_name,
        scope=hook_scope,
    )
    refreshed = persistence.load_tasks(tasks_file)
    post_task = next((t for t in refreshed.tasks if t.id == task_id), None)
    if (
        post_task is not None
        and post_task.status == models.TaskStatus.CANCELLED
    ):
        _restore_cancelled_task(tasks_file, task_id, resolved_dir)
    elif (
        post_task is not None
        and post_task.status == models.TaskStatus.PENDING
        and post_task.pre_tree is None
    ):
        snapshots.prune_snapshots(
            tasks_file, snapshots.collect_task_trees(refreshed.tasks)
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
    active_hooks: list[str] | None,
    working_dir: pathlib.Path | None,
    time_limit: int,
    model_name: str | None = None,
    scope: str | None = None,
    retry_runner_failures: bool = False,
    abort_failed_finalization: bool = False,
    attempt_pre_tree: str | None = None,
    attempt_pre_head: str | None = None,
) -> bool:
    """Handles the aftermath of a task runner exiting.

    Returns True to abort the run loop, False to continue.
    """
    working_dir = working_dir or paths.get_working_dir(tasks_file)
    runner_failed = returncode not in (
        0,
        -15,
        runner.RETURNCODE_TIMEOUT,
    )

    # Record agy's silently lost file creations before hooks judge the task.
    for path in runner.extract_lost_writes(stdout):
        message = f"Runner failed to create {path}: agy rejected the write"
        click.echo(message)
        try:
            progress.add_progress(
                tasks_file, task_id, message[: limits.MAX_PROGRESS_ENTRY_CHARS]
            )
        except Exception:
            # Recording the loss must never take down the loop.
            pass

    # Post-execution validation and heartbeat cleanup
    # This will mark the task as COMPLETED or PENDING based on whether
    # the agent called 'lemming complete'.
    uncounted_failure = runner_failed and not retry_runner_failures
    if uncounted_failure:
        post_task = lifecycle.finish_task_attempt(
            tasks_file, task_id, count_attempt=False
        )
    else:
        post_task = lifecycle.finish_task_attempt(tasks_file, task_id)

    if not post_task:
        click.echo("Error: Task disappeared from roadmap during execution.")
        return True
    uncounted_failure = uncounted_failure and not post_task.requested_status

    if post_task.status == models.TaskStatus.CANCELLED:
        _restore_cancelled_task(tasks_file, task_id, working_dir)
        if verbose:
            click.echo("Task was cancelled. Continuing orchestrator.")
        return False

    # Run orchestrator hooks synchronously if the task requested a status
    # change (completion or failure). This ensures the roadmap is updated
    # and all validation hooks complete before the next task is picked up.
    requested_status = post_task.requested_status
    runner_tree = snapshots.snapshot_tree(tasks_file, working_dir)
    preserve_prior_attempt = bool(
        uncounted_failure
        and attempt_pre_tree
        and post_task.pre_tree
        and attempt_pre_tree != post_task.pre_tree
    )
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        target = next((t for t in data.tasks if t.id == task_id), None)
        if target is not None:
            if requested_status == models.TaskStatus.COMPLETED:
                if runner_tree is not None:
                    target.runner_tree = runner_tree
                    persistence.save_tasks(tasks_file, data)
            elif preserve_prior_attempt:
                if target.runner_tree is None:
                    target.runner_tree = attempt_pre_tree
                    persistence.save_tasks(tasks_file, data)
            elif (
                target.runner_tree is not None or target.hook_trees is not None
            ):
                target.runner_tree = None
                target.hook_trees = None
                persistence.save_tasks(tasks_file, data)
    if requested_status == models.TaskStatus.COMPLETED:
        if runner_tree is not None:
            post_task.runner_tree = runner_tree
    elif preserve_prior_attempt:
        post_task.runner_tree = post_task.runner_tree or attempt_pre_tree
    else:
        post_task.runner_tree = None
        post_task.hook_trees = None

    hook_scope = scope
    if requested_status != models.TaskStatus.COMPLETED:
        target_tree = (
            (attempt_pre_tree or post_task.pre_tree)
            if uncounted_failure
            else (post_task.pre_tree or attempt_pre_tree)
        )
        base_tree = post_task.pre_tree or attempt_pre_tree
        target_head = (
            (attempt_pre_head or post_task.pre_head)
            if uncounted_failure
            else (post_task.pre_head or attempt_pre_head)
        )
        attempt_num = (
            post_task.attempts + 1
            if uncounted_failure
            else max(1, post_task.attempts)
        )
        lifecycle.write_patch_artifact(
            tasks_file,
            task_id,
            f"attempt-{max(1, attempt_num)}.patch",
            base_tree,
            runner_tree,
        )
        if target_tree and (
            runner_tree != target_tree
            or (
                target_head is not None
                and snapshots.head_commit(working_dir) != target_head
            )
        ):
            snapshots.restore_tree(
                tasks_file,
                working_dir,
                target_tree,
                current_tree=runner_tree,
                target_head=target_head,
            )
    elif hook_scope is None and post_task.pre_tree and runner_tree:
        changed = snapshots.changed_files(
            tasks_file, post_task.pre_tree, runner_tree
        )
        hook_scope = scope_mod.describe(changed, working_dir)

    if requested_status:
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
            model_name=model_name,
            scope=hook_scope,
        )

        # A finalizing task keeps its in-progress status until the hooks
        # apply the one it requested, so the outcome only becomes readable
        # here. Judging it from the pre-hook object treats every completed
        # task as a failure and replays its whole log.
        refreshed = persistence.load_tasks(tasks_file)
        post_task = next(
            (t for t in refreshed.tasks if t.id == task_id), post_task
        )
        if post_task.status == models.TaskStatus.CANCELLED:
            _restore_cancelled_task(tasks_file, task_id, working_dir)
            if verbose:
                click.echo("Task was cancelled. Continuing orchestrator.")
            return False
        if (
            post_task.status == models.TaskStatus.PENDING
            and post_task.pre_tree is None
        ):
            snapshots.prune_snapshots(
                tasks_file, snapshots.collect_task_trees(refreshed.tasks)
            )
        if (
            abort_failed_finalization
            and requested_status == models.TaskStatus.COMPLETED
            and post_task.status == models.TaskStatus.PENDING
        ):
            _rewind_to_pre_tree(tasks_file, task_id, working_dir)
            return True

    if post_task.status == models.TaskStatus.COMPLETED:
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
            if post_task.status == models.TaskStatus.CANCELLED:
                if verbose:
                    click.echo("Task was cancelled. Continuing orchestrator.")
                return False
            if verbose:
                click.echo(
                    "Runner exited after SIGTERM. Stopping orchestrator loop."
                )
            return True

        if runner_failed and not requested_status:
            # The runner usually says why it stopped (quota, a rejected
            # model, an auth failure); without this the reason stays buried
            # in the log behind a generic notice.
            reason = runner.extract_error_message(stdout)
            if reason:
                click.echo(f"Runner reported: {reason}")
                try:
                    progress.add_progress(
                        tasks_file, task_id, f"Runner failed: {reason}"
                    )
                except Exception:
                    # Recording the reason must never mask the failure.
                    pass
            if not retry_runner_failures:
                click.echo(
                    "Runner exited unsuccessfully. Task left pending and "
                    "attempt not counted; switch runners or retry later."
                )
                return True

            remaining = post_task.attempts < retries
            outcome = "pending for retry" if remaining else "pending"
            click.echo(f"Runner exited unsuccessfully. Task left {outcome}.")

        will_retry = (
            post_task.status == models.TaskStatus.PENDING
            and post_task.attempts < retries
            and post_task.requested_status is None
        )
        if verbose and will_retry:
            message = (
                "Runner exited unsuccessfully. Retrying..."
                if runner_failed
                else "Runner finished execution but did NOT report "
                "completion. Retrying..."
            )
            click.echo(message)

        # Delay only while a retryable task remains pending.
        if will_retry and retry_delay > 0:
            if verbose:
                click.echo(
                    f"Waiting {retry_delay} seconds before next attempt "
                    "to avoid rate limits..."
                )
            time.sleep(retry_delay)

    return False


_SETTLED_STATUSES = frozenset(
    {
        models.TaskStatus.COMPLETED,
        models.TaskStatus.FAILED,
        models.TaskStatus.CANCELLED,
        models.TaskStatus.SUPERSEDED,
    }
)


def _task_settled(tasks_file: pathlib.Path, task_id: str) -> bool:
    """Reports whether a task has reached a terminal status."""
    data = persistence.load_tasks(tasks_file)
    task = next((t for t in data.tasks if t.id == task_id), None)
    return task is not None and task.status in _SETTLED_STATUSES


def _count_pending(data: models.Roadmap) -> int:
    return sum(t.status == models.TaskStatus.PENDING for t in data.tasks)


def _report_task_limit(data: models.Roadmap, settled: int) -> None:
    """Prints the one line a caller needs after a bounded run stops."""
    task_label = "task" if settled == 1 else "tasks"
    click.echo(
        f"Stopped after {settled} {task_label}; {_count_pending(data)} pending."
    )


def _check_milestone(data: models.Roadmap, until: str) -> bool | None:
    """Decides whether a milestone run should stop, reporting why.

    Returns:
        True to stop successfully, False to stop because the milestone task
        vanished, None to keep running.
    """
    reached = queries.milestone_reached(data.tasks, until)
    if reached is None:
        click.echo(f"Milestone task {until} no longer exists; stopping.")
        return False
    if reached:
        click.echo(
            f"Reached milestone {until}; {_count_pending(data)} pending."
        )
        return True
    return None


def _echo_runner_not_found(runner_name: str) -> None:
    """Prints remediation for a runner command that could not be found."""
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


def run_loop(
    tasks_file: pathlib.Path,
    verbose: bool,
    retry_delay: int,
    yolo: bool,
    no_defaults: bool,
    runner_args: tuple,
    working_dir: pathlib.Path | None = None,
    hooks: list[str] | None = None,
    scope: str | None = None,
    once: bool = False,
    max_tasks: int | None = None,
    until: str | None = None,
) -> bool:
    """Runs pending tasks, returning True only when the roadmap is complete.

    Args:
        tasks_file: Path to the tasks YAML file.
        verbose: Whether to echo prompts and runner output.
        retry_delay: Seconds to wait before retrying an incomplete task.
        yolo: Whether to run the runner in unattended (yolo) mode.
        no_defaults: Whether to skip the runner's default arguments.
        runner_args: Extra arguments forwarded to the runner CLI.
        working_dir: Working directory for the runner processes.
        hooks: Explicit hooks to run after each task, bypassing the project
            and task selection. An empty list runs none. Defaults to
            selecting the active set whenever hooks run, so a running loop
            picks up changes.
        scope: What the hooks should look at, passed through to each.
        once: Run only the first task, through its configured attempt budget.
            Exhaustion leaves it pending, and failed finalization stops rather
            than turning a review placeholder back into runnable work.
        max_tasks: Stop between tasks once this many have reached a terminal
            status, so a caller can run the roadmap one milestone at a time.
            Attempts that leave a task pending do not count.
        until: Exact ID of a milestone task. Stop between tasks once it, or
            every task that replaced it, has settled; stop unsuccessfully if
            it is deleted.

    Returns:
        True when the roadmap ran to completion, the task limit was reached,
        or the milestone was reached.
    """
    working_dir = working_dir or paths.get_working_dir(tasks_file)
    recover_abandoned_tasks(tasks_file, working_dir)
    one_task_id = None
    one_task_config = None
    one_task_runner = None
    one_task_model = None
    settled_count = 0
    while True:
        returncode = 0

        # A drain request stops the loop between tasks, so the task that was
        # already running is never stranded mid-flight.
        if shutdown.drain_requested():
            click.echo("Stop requested; exiting after the current task.")
            return False

        # Reload configuration on each iteration to respond to changes
        # (e.g., from Web UI)
        data = persistence.load_tasks(tasks_file)

        # Bounded runs stop between tasks, after hooks have settled the last
        # one, so the caller sees a consistent roadmap.
        if max_tasks is not None and settled_count >= max_tasks:
            _report_task_limit(data, settled_count)
            return True
        if until is not None:
            milestone = _check_milestone(data, until)
            if milestone is not None:
                return milestone

        retries = data.config.retries
        time_limit = data.config.time_limit
        runner_name = data.config.runner
        model_name = data.config.model
        if once:
            if one_task_config is None:
                one_task_config = (
                    retries,
                    time_limit,
                    runner_name,
                    model_name,
                )
            else:
                retries, time_limit, runner_name, model_name = one_task_config

        if once and one_task_id:
            current_task = next(
                (
                    task
                    for task in data.tasks
                    if task.id == one_task_id
                    and task.status
                    in (
                        models.TaskStatus.PENDING,
                        models.TaskStatus.IN_PROGRESS,
                    )
                ),
                None,
            )
        else:
            current_task = queries.get_pending_task(data)

        if not current_task:
            unfinished_tasks = [
                task
                for task in data.tasks
                if task.status
                in (models.TaskStatus.PENDING, models.TaskStatus.IN_PROGRESS)
            ]
            if unfinished_tasks:
                now = time.time()
                active_task = next(
                    (
                        task
                        for task in unfinished_tasks
                        if lifecycle.is_task_active(task, now)
                    ),
                    None,
                )
                pending_count = sum(
                    task.status == models.TaskStatus.PENDING
                    for task in unfinished_tasks
                )
                if active_task:
                    pending_label = (
                        "task remains" if pending_count == 1 else "tasks remain"
                    )
                    click.echo(
                        f"Queue blocked by active task {active_task.id}; "
                        f"{pending_count} pending {pending_label}."
                    )
                else:
                    click.echo(
                        f"Queue blocked: {len(unfinished_tasks)} unfinished "
                        "task(s) remain, but none can be claimed."
                    )
                return False

            click.echo("All tasks completed!")
            return True

        task_id = current_task.id
        if once and one_task_id is None:
            one_task_id = task_id
            one_task_runner = current_task.runner or runner_name
            one_task_model = current_task.model or model_name

        abandoned = current_task.status == models.TaskStatus.IN_PROGRESS
        # Reclaiming resumes the attempt (see claim_task) unless it was failing.
        resumable = (
            abandoned
            and current_task.requested_status != models.TaskStatus.FAILED
        )
        if current_task.attempts >= retries and not resumable:
            should_abort = _process_exhausted_retries(
                tasks_file=tasks_file,
                task_id=task_id,
                retries=retries,
                runner_name=runner_name,
                model_name=model_name,
                yolo=yolo,
                runner_args=runner_args,
                no_defaults=no_defaults,
                verbose=verbose,
                active_hooks=hooks,
                working_dir=working_dir,
                time_limit=time_limit,
                scope=scope,
            )
            if should_abort:
                return False
            settled_count += _task_settled(tasks_file, task_id)
            continue

        # Add a small random jitter to avoid race conditions between
        # multiple instances
        time.sleep(random.uniform(0.1, 0.5))

        orphan_pid = current_task.pid
        # A runner in its own session can outlive its loop; never run two.
        if (
            abandoned
            and orphan_pid
            and orphan_pid != os.getpid()
            and lifecycle.is_pid_alive(orphan_pid)
        ):
            click.echo(
                f"Task {task_id} was abandoned, but its runner (pid "
                f"{orphan_pid}) is still running; kill it, then rerun."
            )
            return False

        # Try to claim the task
        current_task = lifecycle.claim_task(
            tasks_file, task_id, pid=os.getpid()
        )
        if not current_task:
            if verbose:
                click.echo(
                    f"Task {task_id} already claimed by another instance. "
                    "Skipping."
                )
            continue
        if abandoned:
            click.echo(
                f"Reclaiming abandoned task {task_id}; its runner is gone."
            )

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
                model_name=model_name,
                yolo=yolo,
                runner_args=runner_args,
                no_defaults=no_defaults,
                verbose=verbose,
                active_hooks=hooks,
                working_dir=working_dir,
                time_limit=time_limit,
                scope=scope,
            )
            if once:
                finalized = persistence.load_tasks(tasks_file)
                task = next(
                    (t for t in finalized.tasks if t.id == task_id), None
                )
                if task and task.status == models.TaskStatus.PENDING:
                    _rewind_to_pre_tree(tasks_file, task_id, working_dir)
                return bool(task and task.status == models.TaskStatus.COMPLETED)
            settled_count += _task_settled(tasks_file, task_id)
            continue

        prompt = prompts.prepare_prompt(
            data, current_task, tasks_file, time_limit
        )

        if verbose:
            click.secho("\n=== Runner Prompt ===", fg="blue", bold=True)
            click.echo(prompt)
            click.secho("====================\n", fg="blue", bold=True)

        selected_runner = current_task.runner or runner_name
        selected_model = current_task.model or model_name
        if once:
            selected_runner = one_task_runner or selected_runner
            selected_model = one_task_model
        cmd = runner.build_runner_command(
            selected_runner,
            prompt,
            yolo,
            runner_args,
            no_defaults,
            verbose=verbose,
            time_limit=time_limit,
            working_dir=working_dir,
            model=selected_model,
        )

        # Record what actually launched before it runs, so the provenance
        # survives a crash or an interrupted attempt.
        lifecycle.record_resolved_command(
            tasks_file, task_id, runner.describe_command(cmd, prompt)
        )

        attempt_pre_tree = snapshots.snapshot_tree(tasks_file, working_dir)
        attempt_pre_head = snapshots.head_commit(working_dir)
        if (
            (
                current_task.pre_tree is None
                and (
                    attempt_pre_tree is not None or attempt_pre_head is not None
                )
            )
            or current_task.runner_tree is not None
            or current_task.hook_trees is not None
        ):
            with persistence.lock_tasks(tasks_file):
                latest = persistence.load_tasks(tasks_file)
                target = next(
                    (t for t in latest.tasks if t.id == task_id), None
                )
                if target is not None:
                    if target.pre_tree is None:
                        target.pre_tree = attempt_pre_tree
                        target.pre_head = attempt_pre_head
                    target.runner_tree = None
                    target.hook_trees = None
                    persistence.save_tasks(tasks_file, latest)
            if current_task.pre_tree is None:
                current_task.pre_tree = attempt_pre_tree
                current_task.pre_head = attempt_pre_head
            current_task.runner_tree = None
            current_task.hook_trees = None

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
                    f"\n{selected_runner.capitalize()} execution failed "
                    f"with exit code {returncode}"
                )
                if returncode == 127:
                    _echo_runner_not_found(selected_runner)
        except persistence.CorruptedTasksError:
            # Retrying cannot help an unreadable roadmap, and counting the
            # attempt would spend the task's retries on the wrong failure.
            raise
        except Exception as e:
            # Leaving the return code at its initialised zero reports a
            # runner that never started as one that exited cleanly, so the
            # task burns every retry and is marked failed for work nothing
            # ever attempted. No signal downstream can tell them apart.
            returncode = runner.RETURNCODE_LAUNCH_FAILED
            click.echo(
                f"\nAn error occurred while executing {selected_runner}: {e}"
            )
            # Popen raises this, not exit code 127, for a missing executable.
            if isinstance(e, FileNotFoundError) and e.filename == cmd[0]:
                _echo_runner_not_found(selected_runner)

        should_abort = _handle_runner_exit(
            tasks_file=tasks_file,
            task_id=task_id,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            retries=retries,
            retry_delay=retry_delay,
            runner_name=selected_runner,
            model_name=selected_model,
            yolo=yolo,
            runner_args=runner_args,
            no_defaults=no_defaults,
            verbose=verbose,
            active_hooks=hooks,
            working_dir=working_dir,
            time_limit=time_limit,
            scope=scope,
            retry_runner_failures=once,
            abort_failed_finalization=once,
            attempt_pre_tree=attempt_pre_tree,
            attempt_pre_head=attempt_pre_head,
        )
        if should_abort:
            return False
        settled_count += _task_settled(tasks_file, task_id)

        if once:
            updated = persistence.load_tasks(tasks_file)
            task = next((t for t in updated.tasks if t.id == task_id), None)
            if not task or task.status != models.TaskStatus.PENDING:
                return bool(task and task.status == models.TaskStatus.COMPLETED)
            if task.attempts >= retries:
                _rewind_to_pre_tree(tasks_file, task_id, working_dir)
                return False


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
