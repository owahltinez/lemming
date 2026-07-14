"""In-container entry point that replays the orchestrator's hook flow.

The harness invokes this module inside the eval container so the hook under
eval runs through the exact same code path the orchestrator uses in
production: mark the finished task in progress, mark exhausted failures as
failed, then run the hook and apply the final status.
"""

import pathlib

import click

from .. import models, tasks
from ..hooks import run_hooks


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
def main(
    tasks_file: pathlib.Path,
    task_id: str,
    hook: str,
    outcome: str,
    runner_name: str,
    time_limit: int,
) -> None:
    """Runs a single hook against a fixture exactly like the orchestrator."""
    final_status = (
        models.TaskStatus.COMPLETED
        if outcome == "completed"
        else models.TaskStatus.FAILED
    )

    # Mirror orchestrator._process_exhausted_retries: hooks run while the
    # task is in progress (fresh heartbeat), and exhausted failures are
    # marked FAILED before the hook sees them.
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

    # A dead runner (auth failure, missing binary, timeout) leaves the
    # workspace untouched, which can look identical to a well-behaved
    # fast-exiting agent. Fail the trial loudly instead of false-passing.
    failed = {name: code for name, code in exit_codes.items() if code != 0}
    if failed:
        raise click.ClickException(f"Hook runner failed: {failed}")


if __name__ == "__main__":
    main()
