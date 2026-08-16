"""In-container entry point that runs one eval trial.

The harness invokes this module inside the eval container so the prompt
under eval runs through the exact same code path production uses.

In hook mode that is the orchestrator's hook flow: mark the finished task
in progress, mark exhausted failures as failed, then run the hook and apply
the final status. In task mode it is ``lemming exec``: an agent is handed
the scenario's prompt as a one-shot and the workspace is graded afterwards.
"""

import json
import pathlib

import click

from .. import models, runner, tasks
from ..cli import cli as lemming_cli
from ..orchestrator import run_hooks


def _write_result(
    result_file: pathlib.Path | None, exit_codes: dict[str, int]
) -> None:
    """Records how the run ended for the harness to classify.

    A trial that fails because the runner never started, or because it ran
    out of time, says nothing about the agent's judgement. Both look
    identical to a behavioural failure once they reach the grader, so the
    distinction has to be captured here while the exit codes still mean
    something.

    Args:
        result_file: Where to write the record; skipped when None.
        exit_codes: Runner exit codes, keyed by hook name or by "task".
    """
    if result_file is None:
        return

    codes = list(exit_codes.values())
    payload = {
        "exit_codes": exit_codes,
        "launch_failed": runner.RETURNCODE_LAUNCH_FAILED in codes,
        "timed_out": runner.RETURNCODE_TIMEOUT in codes,
    }
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(payload, indent=2))


def _run_task(
    workspace: pathlib.Path,
    prompt: str,
    runner_name: str,
    time_limit: int,
) -> dict[str, int]:
    """Runs the scenario's prompt as a one-shot against the workspace.

    This is ``lemming exec`` invoked in process rather than a second
    lemming: exec already builds the ephemeral roadmap, drives exactly one
    attempt, and keeps its state under LEMMING_HOME, which the harness
    mounts back out for inspection.

    Args:
        workspace: The repository the agent works in.
        prompt: The work the agent is asked to carry out.
        runner_name: Runner CLI to drive, with any arguments.
        time_limit: Minutes before the agent is killed.

    Returns:
        The agent's exit code, keyed like a hook's so both modes report the
        same record.
    """
    # The exit code that tells "never started" from "ran and declined"
    # is consumed inside the run loop, so it is observed by wrapping the
    # module attribute; safe because this runs one trial per process.
    codes: list[int] = []
    original = runner.run_with_heartbeat

    def recording(*args, **kwargs):
        try:
            result = original(*args, **kwargs)
        except Exception:
            codes.append(runner.RETURNCODE_LAUNCH_FAILED)
            raise
        codes.append(result[0])
        return result

    runner.run_with_heartbeat = recording
    try:
        lemming_cli.main(
            args=[
                "--project-dir",
                str(workspace),
                "exec",
                prompt,
                "--runner",
                runner_name,
                "--time-limit",
                str(time_limit),
                # The runner log is the only account of the run.
                "--keep",
            ],
            prog_name="lemming",
            standalone_mode=False,
        )
    finally:
        runner.run_with_heartbeat = original

    # No agent process at all counts as a failure to launch.
    return {"task": codes[-1] if codes else runner.RETURNCODE_LAUNCH_FAILED}


def _run_hook(
    tasks_file: pathlib.Path,
    task_id: str,
    hook: str,
    outcome: str,
    runner_name: str,
    time_limit: int,
) -> dict[str, int]:
    """Runs one hook against a fixture exactly like the orchestrator.

    Args:
        tasks_file: The fixture tasks file.
        task_id: ID of the finished task the hook reacts to.
        hook: Name of the hook to run.
        outcome: Terminal status of the finished task.
        runner_name: Runner CLI to drive, with any arguments.
        time_limit: Minutes before the hook runner is killed.

    Returns:
        Per-hook exit codes as reported by run_hooks.
    """
    final_status = (
        models.TaskStatus.COMPLETED
        if outcome == "completed"
        else models.TaskStatus.FAILED
    )

    # Mirror orchestrator._process_exhausted_retries before the hook.
    tasks.mark_task_in_progress(tasks_file, task_id)
    if final_status == models.TaskStatus.FAILED:
        tasks.update_task(tasks_file, task_id, status=models.TaskStatus.FAILED)

    return run_hooks(
        tasks_file,
        task_id,
        runner_name,
        yolo=True,
        runner_args=(),
        no_defaults=False,
        verbose=True,
        hooks=[hook],
        working_dir=tasks_file.parent,
        final_status=final_status,
        time_limit=time_limit,
    )


@click.command()
@click.option(
    "--mode",
    type=click.Choice(["hook", "task"]),
    default="hook",
    show_default=True,
    help="Run an orchestrator hook, or a one-shot task.",
)
@click.option(
    "--tasks-file",
    type=click.Path(path_type=pathlib.Path, exists=True),
    default=None,
    help="Hook mode: path to the fixture tasks file.",
)
@click.option(
    "--task-id", default=None, help="Hook mode: ID of the finished task."
)
@click.option(
    "--hook", default=None, help="Hook mode: hook to run (e.g. roadmap)."
)
@click.option(
    "--outcome",
    type=click.Choice(["completed", "failed"]),
    default=None,
    help="Hook mode: terminal status of the finished task.",
)
@click.option(
    "--workspace",
    type=click.Path(path_type=pathlib.Path, exists=True, file_okay=False),
    default=None,
    help="Task mode: repository the agent works in.",
)
@click.option(
    "--prompt", default=None, help="Task mode: the work to carry out."
)
@click.option(
    "--runner", "runner_name", required=True, help="Runner CLI to drive."
)
@click.option(
    "--time-limit",
    type=int,
    default=15,
    help="Time limit in minutes for the agent run.",
)
@click.option(
    "--result-file",
    type=click.Path(path_type=pathlib.Path),
    default=None,
    help="Where to record runner exit codes for the harness.",
)
def main(
    mode: str,
    tasks_file: pathlib.Path | None,
    task_id: str | None,
    hook: str | None,
    outcome: str | None,
    workspace: pathlib.Path | None,
    prompt: str | None,
    runner_name: str,
    time_limit: int,
    result_file: pathlib.Path | None,
) -> None:
    """Runs one trial: a hook against a fixture, or a one-shot task."""
    if mode == "task":
        if not workspace or not prompt:
            raise click.UsageError(
                "--mode task requires --workspace and --prompt."
            )
        exit_codes = _run_task(workspace, prompt, runner_name, time_limit)
    else:
        if not tasks_file or not task_id or not hook or not outcome:
            raise click.UsageError(
                "--mode hook requires --tasks-file, --task-id, --hook and "
                "--outcome."
            )
        exit_codes = _run_hook(
            tasks_file, task_id, hook, outcome, runner_name, time_limit
        )

    _write_result(result_file, exit_codes)

    # Fail loudly: a dead runner leaves a workspace that looks clean.
    failed = {name: code for name, code in exit_codes.items() if code != 0}
    if failed:
        raise click.ClickException(
            f"{mode.capitalize()} runner failed: {failed}"
        )


if __name__ == "__main__":
    main()
