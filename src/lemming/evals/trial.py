"""In-container entry point that replays the orchestrator's hook flow.

The harness invokes this module inside the eval container so the hook under
eval runs through the exact same code path the orchestrator uses in
production: mark the finished task in progress, mark exhausted failures as
failed, then run the hook and apply the final status.
"""

import json
import pathlib

import click

from .. import models, runner, tasks
from ..orchestrator import run_hooks


def _write_result(
    result_file: pathlib.Path | None, exit_codes: dict[str, int]
) -> None:
    """Records how the hook run ended for the harness to classify.

    A trial that fails because the runner never started, or because it ran
    out of time, says nothing about the agent's judgement. Both look
    identical to a behavioural failure once they reach the grader, so the
    distinction has to be captured here while the exit codes still mean
    something.

    Args:
        result_file: Where to write the record; skipped when None.
        exit_codes: Per-hook exit codes as reported by run_hooks.
    """
    if result_file is None:
        return

    codes = list(exit_codes.values())
    payload = {
        "exit_codes": exit_codes,
        "launch_failed": -1 in codes,
        "timed_out": runner.RETURNCODE_TIMEOUT in codes,
    }
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(payload, indent=2))


@click.command()
@click.option(
    "--tasks-file",
    type=click.Path(path_type=pathlib.Path, exists=True),
    required=True,
    help="Path to the fixture tasks file.",
)
@click.option("--task-id", required=True, help="ID of the finished task.")
@click.option("--hook", required=True, help="Hook to run (e.g. roadmap).")
@click.option(
    "--outcome",
    type=click.Choice(["completed", "failed"]),
    required=True,
    help="Terminal status of the finished task the hook reacts to.",
)
@click.option(
    "--runner", "runner_name", required=True, help="Runner CLI to drive."
)
@click.option(
    "--time-limit",
    type=int,
    default=15,
    help="Time limit in minutes for the hook run.",
)
@click.option(
    "--result-file",
    type=click.Path(path_type=pathlib.Path),
    default=None,
    help="Where to record hook exit codes for the harness.",
)
def main(
    tasks_file: pathlib.Path,
    task_id: str,
    hook: str,
    outcome: str,
    runner_name: str,
    time_limit: int,
    result_file: pathlib.Path | None,
) -> None:
    """Runs a single hook against a fixture exactly like the orchestrator."""
    final_status = (
        models.TaskStatus.COMPLETED
        if outcome == "completed"
        else models.TaskStatus.FAILED
    )

    # Mirror orchestrator._process_exhausted_retries before the hook.
    tasks.mark_task_in_progress(tasks_file, task_id)
    if final_status == models.TaskStatus.FAILED:
        tasks.update_task(tasks_file, task_id, status=models.TaskStatus.FAILED)

    exit_codes = run_hooks(
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

    _write_result(result_file, exit_codes)

    # Fail loudly: a dead runner leaves a workspace that looks clean.
    failed = {name: code for name, code in exit_codes.items() if code != 0}
    if failed:
        raise click.ClickException(f"Hook runner failed: {failed}")


if __name__ == "__main__":
    main()
