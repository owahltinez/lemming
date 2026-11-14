"""Orchestrator loop that executes pending tasks via runners and hooks."""

import os
import pathlib
import random
import time

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
from .tasks import lifecycle, limits, progress, queries, state_machine

# Minutes a single hook may run, so a hung hook cannot burn a task's budget.
HOOK_TIME_LIMIT = 15


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
            task.status in models.ACTIVE_STATUSES
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
    time_limit: int = 0,
    model_name: str | None = None,
    scope: str | None = None,
) -> dict[str, int]:
    """Executes orchestrator hooks for a finished task.

    A completing or failing task is concluded once they finish, unless a hook
    already moved it elsewhere.

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
    finalizing = task.status in (
        models.TaskStatus.COMPLETING,
        models.TaskStatus.FAILING,
    )

    # Use provided hooks or the project's and task's selection
    active_hooks = (
        hooks
        if hooks is not None
        else select_hooks(tasks_file, data.config.hooks, task.hooks)
    )

    # A oneshot task buys its latency back by skipping the post-task
    # review, but a failing one still gets failure-hook recovery below.
    if task.oneshot and task.status == models.TaskStatus.COMPLETING:
        active_hooks = []

    # On failure, only failure hooks (9x priority prefix) run
    if task.status == models.TaskStatus.FAILING:
        active_hooks = [
            h
            for h in active_hooks
            if get_hook_priority(h, tasks_file) >= FAILURE_HOOK_PRIORITY
        ]

    if not active_hooks:
        if finalizing:
            state_machine.conclude(tasks_file, task_id, working_dir, True)
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
            and finalizing
            and task.status not in models.ACTIVE_STATUSES
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

    # A hook may already have moved the task on (the roadmap hook resetting
    # a failed task for a new approach); conclude leaves that alone.
    if finalizing:
        current = next(
            (
                t
                for t in persistence.load_tasks(tasks_file).tasks
                if t.id == task_id
            ),
            None,
        )
        if current is None:
            click.echo(
                f"Task {task_id} was removed before it could be "
                "finalized — the orchestrator likely restructured "
                "the plan."
            )
            return exit_codes

        # A killed or crashed hook (e.g. the model became unavailable)
        # leaves the completion unverified, and a rejection refuses it
        # outright; either way the attempt is retried. Failure finalization
        # proceeds regardless: those tasks have already given up.
        failed_hooks = [h for h, code in exit_codes.items() if code != 0]
        if current.status == models.TaskStatus.COMPLETING and (
            failed_hooks or current.rejection
        ):
            reasons = []
            if failed_hooks:
                reasons.append(f"hooks failed ({', '.join(failed_hooks)})")
            if current.rejection:
                # The reason is already recorded against the hook that gave
                # it; repeating it here would crowd the handful of entries
                # the next attempt is shown.
                reasons.append("was rejected")
            detail = "; ".join(reasons)
            progress.add_progress(
                tasks_file,
                task_id,
                f"Finalization {detail}; task reverted to pending.",
            )
            state_machine.conclude(tasks_file, task_id, working_dir, False)
            click.echo(
                f"Task {task_id} finalization {detail}. Reverting to pending."
            )
        else:
            state_machine.conclude(tasks_file, task_id, working_dir, True)

    return exit_codes


def recover_abandoned_tasks(
    tasks_file: pathlib.Path,
    working_dir: pathlib.Path | None = None,
    *,
    exiting: bool = False,
) -> None:
    """Settles active tasks whose owner died at their last checkpoint.

    The loop that owned them runs this on its way out, and the next loop on
    startup in case the last one was killed before it could. A cut-off run
    goes back to pending without using up an attempt; a review keeps its
    place and is resumed by the next run. A review with no recorded owner
    was already released this way and is left alone.

    Args:
        tasks_file: Path to the tasks YAML file.
        working_dir: Workspace to rewind; defaults to the tasks file's.
        exiting: Whether the caller is the owning loop, alive until now
            rather than only until its last heartbeat.
    """
    working_dir = working_dir or paths.get_working_dir(tasks_file)
    for task in persistence.load_tasks(tasks_file).tasks:
        if task.status not in models.ACTIVE_STATUSES or (
            task.pid is None and task.status != models.TaskStatus.RUNNING
        ):
            continue
        if (
            task.pid is not None
            and task.pid != os.getpid()
            and lifecycle.is_pid_alive(task.pid)
        ):
            continue
        click.echo(f"Task {task.id} was cut off; rewinding it to a checkpoint.")
        state_machine.settle(
            tasks_file,
            task.id,
            working_dir,
            counted=False,
            released_at=time.time()
            if exiting
            else max(task.last_heartbeat or 0, task.last_started_at or 0),
        )


def _review(
    tasks_file: pathlib.Path,
    task_id: str,
    runner_name: str,
    yolo: bool,
    runner_args: tuple,
    no_defaults: bool,
    verbose: bool,
    active_hooks: list[str] | None,
    working_dir: pathlib.Path,
    time_limit: int,
    model_name: str | None = None,
    scope: str | None = None,
) -> models.Task | None:
    """Runs the hooks over a settled completing or failing task.

    Returns:
        The task as the hooks left it, or None if it was deleted.
    """
    task = next(
        (
            t
            for t in persistence.load_tasks(tasks_file).tasks
            if t.id == task_id
        ),
        None,
    )
    if (
        scope is None
        and task is not None
        and task.status == models.TaskStatus.COMPLETING
        and task.pre_tree
        and task.runner_tree
    ):
        changed = snapshots.changed_files(
            tasks_file, task.pre_tree, task.runner_tree
        )
        scope = scope_mod.describe(changed, working_dir)

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
        time_limit=time_limit,
        model_name=model_name,
        scope=scope,
    )

    refreshed = persistence.load_tasks(tasks_file)
    task = next((t for t in refreshed.tasks if t.id == task_id), None)
    if task is not None and task.status == models.TaskStatus.CANCELLED:
        # A hook cancelling the task may have killed itself before it could
        # restore the workspace.
        task = state_machine.settle(tasks_file, task_id, working_dir)
    elif (
        task is not None
        and task.status == models.TaskStatus.PENDING
        and task.pre_tree is None
    ):
        snapshots.prune_snapshots(
            tasks_file, snapshots.collect_task_trees(refreshed.tasks)
        )
    return task


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
    """Fails a task that ran out of attempts, giving failure hooks a say.

    Returns True to abort the run loop, False to continue.
    """
    working_dir = working_dir or paths.get_working_dir(tasks_file)
    # Failure hooks such as the roadmap may still heal it before we abort.
    lifecycle.claim_task(
        tasks_file,
        task_id,
        os.getpid(),
        working_dir,
        status=models.TaskStatus.FAILING,
    )
    state_machine.settle(tasks_file, task_id, working_dir)
    task = _review(
        tasks_file,
        task_id,
        runner_name,
        yolo,
        runner_args,
        no_defaults,
        verbose,
        active_hooks,
        working_dir,
        time_limit,
        model_name=model_name,
        scope=scope,
    )
    if (
        task is not None
        and task.status.outcome == models.TaskStatus.FAILED
        and task.attempts >= retries
    ):
        click.echo(
            f"\nTask {task_id} failed after {retries} attempts. Aborting run."
        )
        return True

    # Orchestrator healed or superseded it — continue the loop.
    click.echo(f"Orchestrator intervened on task {task_id}. Continuing...")
    return False


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
    claimed: models.Task | None = None,
) -> bool:
    """Handles the aftermath of a task runner exiting.

    The claimed task, when given, holds the checkpoint the run started from.
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

    # A run that never requested an outcome goes back to pending, and only
    # a review (or a cancel) keeps the task from it.
    post_task = state_machine.settle(
        tasks_file,
        task_id,
        working_dir,
        counted=not runner_failed or retry_runner_failures,
    )
    if not post_task:
        click.echo("Error: Task disappeared from roadmap during execution.")
        return True
    if claimed is not None and (
        post_task.status
        in (models.TaskStatus.FAILED, models.TaskStatus.SUPERSEDED)
        or (
            post_task.status == models.TaskStatus.PENDING
            and not post_task.pre_tree
        )
    ):
        # Moved by hand mid-run (reset, reopened, superseded, force-failed),
        # taking its checkpoint along; the claim still holds it.
        state_machine.rewind(
            tasks_file,
            task_id,
            working_dir,
            claimed.pre_tree,
            claimed.pre_head,
            f"attempt-{max(1, claimed.attempts)}.patch",
        )
    requested = post_task.status in (
        models.TaskStatus.COMPLETING,
        models.TaskStatus.FAILING,
    )
    completing = post_task.status == models.TaskStatus.COMPLETING

    # Hooks run synchronously, so the roadmap is updated and every review
    # has finished before the next task is picked up.
    if requested:
        post_task = (
            _review(
                tasks_file,
                task_id,
                runner_name,
                yolo,
                runner_args,
                no_defaults,
                verbose,
                active_hooks,
                working_dir,
                time_limit,
                model_name=model_name,
                scope=scope,
            )
            or post_task
        )
        if (
            abort_failed_finalization
            and completing
            and post_task.status == models.TaskStatus.PENDING
        ):
            return True

    if post_task.status == models.TaskStatus.CANCELLED:
        if verbose:
            click.echo("Task was cancelled. Continuing orchestrator.")
        return False

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
            if verbose:
                click.echo(
                    "Runner exited after SIGTERM. Stopping orchestrator loop."
                )
            return True

        if runner_failed and not requested:
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
                    and task.status not in models.TERMINAL_STATUSES
                ),
                None,
            )
        else:
            current_task = queries.get_pending_task(data)

        if not current_task:
            unfinished_tasks = [
                task
                for task in data.tasks
                if task.status not in models.TERMINAL_STATUSES
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

        orphan_pid = current_task.pid
        # A runner or hook in its own session can outlive its loop; never
        # run two.
        if (
            current_task.status in models.ACTIVE_STATUSES
            and orphan_pid
            and orphan_pid != os.getpid()
            and lifecycle.is_pid_alive(orphan_pid)
        ):
            click.echo(
                f"Task {task_id} was abandoned, but its runner (pid "
                f"{orphan_pid}) is still running; kill it, then rerun."
            )
            return False
        if current_task.status == models.TaskStatus.RUNNING:
            recover_abandoned_tasks(tasks_file, working_dir)
            continue

        if current_task.attempts >= retries and current_task.status in (
            models.TaskStatus.PENDING,
            models.TaskStatus.FAILING,
        ):
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

        current_task = lifecycle.claim_task(
            tasks_file, task_id, os.getpid(), working_dir
        )
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

        # A review left unowned resumes without running the agent again.
        if current_task.status != models.TaskStatus.RUNNING:
            if verbose:
                click.echo(
                    f"Task {task_id} resumed while {current_task.status}. "
                    "Skipping runner."
                )
            state_machine.settle(tasks_file, task_id, working_dir)
            task = _review(
                tasks_file,
                task_id,
                runner_name,
                yolo,
                runner_args,
                no_defaults,
                verbose,
                hooks,
                working_dir,
                time_limit,
                model_name=model_name,
                scope=scope,
            )
            if once:
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
            claimed=current_task,
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
