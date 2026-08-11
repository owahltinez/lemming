"""CLI commands for the orchestrator loop and the web interface."""

import copy
import os
import pathlib
import secrets
import signal
import sys
import threading
import time

import click

from .. import paths, providers, shutdown, tasks
from ..orchestrator import parse_timeout, run_loop
from .main import cli

# How long `lemming stop` waits for the loop to shut down before giving up.
LOOP_EXIT_TIMEOUT = 30.0


@cli.command(
    short_help="Run the autonomous task execution loop",
)
@click.option(
    "--retry-delay",
    default=10,
    help="Seconds to wait before retrying an incomplete task.",
)
@click.option(
    "--yolo/--no-yolo",
    default=True,
    help="Run the runner in YOLO/auto-approve mode.",
)
@click.option(
    "--env",
    multiple=True,
    help="Environment variables to set for the runner (e.g. --env KEY=VALUE).",
)
@click.option(
    "--no-defaults",
    is_flag=True,
    help="Do not auto-inject default flags (like --yolo) based on runner name.",
)
@click.argument("runner_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run(
    ctx: click.Context,
    retry_delay: int,
    yolo: bool,
    env: tuple,
    no_defaults: bool,
    runner_args: tuple,
) -> None:
    """Starts the orchestrator loop to autonomously execute pending tasks."""
    tasks_file = ctx.obj["TASKS_FILE"]
    verbose = ctx.obj["VERBOSE"]

    # Determine the project's working directory
    working_dir = paths.get_working_dir(tasks_file)

    # Parse environment overrides
    env_overrides = {}
    for e in env:
        if "=" in e:
            k, v = e.split("=", 1)
            env_overrides[k] = v
        else:
            env_overrides[e] = ""

    if env_overrides:
        os.environ.update(env_overrides)

    # Handle stop requests before claiming work, so a SIGTERM arriving mid-task
    # takes the runner down instead of orphaning it.
    shutdown.clear_drain()
    shutdown.install_handlers()

    completed = False
    try:
        tasks.acquire_loop_lock(tasks_file)
    except tasks.LoopAlreadyRunningError as e:
        click.echo(f"Error: {e}")
        ctx.exit(1)
    try:
        completed = run_loop(
            tasks_file,
            verbose,
            retry_delay,
            yolo,
            no_defaults,
            runner_args,
            working_dir=working_dir,
        )
    finally:
        tasks.release_loop_lock(tasks_file)
    if not completed:
        ctx.exit(1)


def _wait_for_loop_exit(pid: int, timeout: float = LOOP_EXIT_TIMEOUT) -> bool:
    """Waits for the orchestrator process to exit.

    Args:
        pid: PID of the orchestrator loop.
        timeout: Seconds to wait before giving up.

    Returns:
        True if the loop exited within the timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not tasks.is_pid_alive(pid):
            return True
        time.sleep(0.1)
    return not tasks.is_pid_alive(pid)


def _release_stopped_tasks(tasks_file: pathlib.Path) -> list[str]:
    """Returns tasks abandoned by the stopped loop to the pending queue.

    A task left in_progress with a dead runner blocks the next `lemming run`
    until it goes stale, so an intentional stop clears it immediately.

    Args:
        tasks_file: Path to the tasks YAML file.

    Returns:
        IDs of the tasks that were reverted.
    """
    now = time.time()
    reverted = []

    for task in tasks.load_tasks(tasks_file).tasks:
        is_unfinished = (
            task.status == tasks.TaskStatus.IN_PROGRESS or task.requested_status
        )
        if is_unfinished and not tasks.is_task_active(task, now):
            tasks.revert_task_to_pending(tasks_file, task.id)
            reverted.append(task.id)

    return reverted


@cli.command(short_help="Stop the running orchestrator loop")
@click.option(
    "--after-current-task",
    is_flag=True,
    help="Let the running task finish, then stop before claiming another.",
)
@click.pass_context
def stop(ctx: click.Context, after_current_task: bool) -> None:
    """Stops the orchestrator loop started by `lemming run`.

    By default the running task is interrupted and returned to the queue.
    With --after-current-task the loop drains instead, which is the safe way
    to reconfigure a runner or model without stranding work in flight.
    """
    tasks_file = ctx.obj["TASKS_FILE"]

    loop_pid = tasks.get_loop_pid(tasks_file)
    if loop_pid is None or not tasks.is_pid_alive(loop_pid):
        click.echo("No orchestrator loop is running.")
        return

    # A drain leaves the in-flight task alone; an immediate stop tears the
    # runner down and hands its task back to the queue.
    if after_current_task:
        os.kill(loop_pid, shutdown.DRAIN_SIGNAL)
        click.echo(
            f"Stop requested; loop {loop_pid} will exit after the current task."
        )
        return

    os.kill(loop_pid, signal.SIGTERM)
    if not _wait_for_loop_exit(loop_pid):
        click.echo(
            f"Loop {loop_pid} did not exit within "
            f"{LOOP_EXIT_TIMEOUT:g}s; leaving task state untouched."
        )
        ctx.exit(1)

    reverted = _release_stopped_tasks(tasks_file)
    click.echo(f"Stopped orchestrator loop {loop_pid}.")
    for task_id in reverted:
        click.echo(f"Returned task {task_id} to the queue.")


@cli.command(short_help="Launch the web interface")
@click.option("--port", default=8999, help="Port to run the server on.")
@click.option("--host", default="127.0.0.1", help="Host to bind the server to.")
@click.option(
    "--tunnel",
    default=None,
    type=click.Choice(["cloudflare", "tailscale"]),
    help="Expose via a public tunnel (cloudflare or tailscale).",
)
@click.option(
    "--timeout",
    default=None,
    help=(
        "Auto-shutdown after duration (e.g., '8h', '30m', '0' to disable)."
        " Defaults to '8h' when --tunnel is used."
    ),
)
@click.pass_context
def serve(
    ctx: click.Context,
    port: int,
    host: str,
    tunnel: str | None,
    timeout: str | None,
):
    """Launches the local web dashboard for monitoring and interaction.

    Optionally exposes it to the public internet via --tunnel.
    """
    # Lazy imports: the api package builds the FastAPI app and mounts static
    # assets at import time, so keep the server stack out of CLI startup.
    import uvicorn  # noqa: PLC0415
    import uvicorn.config  # noqa: PLC0415

    from .. import api  # noqa: PLC0415

    api.app.state.tasks_file = ctx.obj["TASKS_FILE"]
    api.app.state.verbose = ctx.obj["VERBOSE"]
    api.app.state.root = pathlib.Path.cwd().resolve()

    tunnel_proc = None
    if tunnel:
        click.echo(f"[ Lemming ] Starting local server on port {port}...")
        click.echo(
            f"[ Lemming ] Initiating public tunnel via {tunnel.capitalize()}..."
        )

        tunnel_proc = (
            providers.CloudflareProvider()
            if tunnel == "cloudflare"
            else providers.TailscaleProvider()
        )
        try:
            public_url = tunnel_proc.start(port)
        except Exception as e:
            click.echo(f"[ Lemming ] Error starting tunnel: {e}", err=True)
            sys.exit(1)

        token = secrets.token_urlsafe(32)
        api.app.state.share_token = token

        click.echo("[ Lemming ] ")
        click.echo("[ Lemming ] ⚠️  SECURITY WARNING ")
        click.echo(
            "[ Lemming ] Your Lemming instance is being exposed to the"
            " public internet."
        )
        click.echo(
            "[ Lemming ] Token-based authentication has been automatically"
            " enabled."
        )
        click.echo("[ Lemming ] ")
        click.echo(
            "[ Lemming ] 🌐 Share this exact, secure link with the remote user:"
        )
        click.echo(f"[ Lemming ] 👉 {public_url}?token={token}")
        # The token is required locally too, so echo a usable local link.
        click.echo(f"[ Lemming ] 🖥️  Local: http://{host}:{port}?token={token}")
        click.echo("")
    else:
        click.echo(f"Launching Lemming UI at http://{host}:{port}")

    # Default timeout to 8h for tunnel mode, 0 (disabled) for local mode.
    timeout_str = timeout if timeout is not None else ("8h" if tunnel else "0")
    timeout_seconds = parse_timeout(timeout_str)

    if timeout_seconds > 0:
        click.echo(
            "[ Lemming ] The server will automatically shut down"
            f" in {timeout_str}."
        )

        def monitor():
            time.sleep(timeout_seconds)
            click.echo(
                "\n[ Lemming ] Timeout reached. Waiting for tasks to finish..."
            )
            if tunnel_proc:
                tunnel_proc.stop()

            tasks_file = api.app.state.tasks_file
            while True:
                try:
                    project_data = tasks.get_project_data(tasks_file)
                except tasks.CorruptedTasksError as e:
                    # This runs in a daemon thread: without this the shutdown
                    # would die with a traceback and leave the server up.
                    click.echo(f"[ Lemming ] {e}", err=True)
                    break
                if not project_data.loop_running:
                    break
                time.sleep(5)

            click.echo("[ Lemming ] All tasks finished. Exiting.")
            os._exit(0)

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()

    if tunnel:
        click.echo(
            "[ Lemming ] Press Ctrl+C to manually close the tunnel and"
            " shut down the server."
        )

    # Suppress repetitive access-log lines from UI polling endpoints.
    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["filters"] = {
        "quiet_poll": {"()": "lemming.api.QuietPollFilter"},
    }
    log_config["handlers"]["access"]["filters"] = ["quiet_poll"]

    try:
        uvicorn.run(api.app, host=host, port=port, log_config=log_config)
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel_proc:
            tunnel_proc.stop()
