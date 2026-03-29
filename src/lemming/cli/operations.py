import os
import pathlib
import threading
import time
import click
from .main import cli
from .. import tasks
from .. import paths
from ..orchestrator import run_loop, parse_timeout


@cli.command(
    short_help="Run the autonomous task execution loop",
)
@click.option(
    "--retry-delay",
    default=10,
    help="Seconds to wait before retrying a failed task (to handle rate limits).",
)
@click.option(
    "--yolo/--no-yolo", default=True, help="Run the runner in YOLO/auto-approve mode."
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
    """Starts the orchestrator loop to autonomously execute pending tasks.

    Args:
        retry_delay: Delay between retries.
        yolo: If True, skip runner confirmations.
        env: Environment variables to inject.
        no_defaults: Skip default flag injection.
        runner_args: Raw arguments passed directly to the runner.
    """
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

    tasks.acquire_loop_lock(tasks_file)
    try:
        run_loop(
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
    help="Auto-shutdown after duration (e.g., '8h', '30m', '0' to disable). Defaults to '8h' when --tunnel is used.",
)
@click.pass_context
def serve(
    ctx: click.Context, port: int, host: str, tunnel: str | None, timeout: str | None
):
    """Launches the local web dashboard for monitoring and interaction.

    Optionally exposes it to the public internet via --tunnel.
    """
    import copy
    import secrets
    import sys

    import uvicorn
    import uvicorn.config

    from .. import api
    from .. import providers

    api.app.state.tasks_file = ctx.obj["TASKS_FILE"]
    api.app.state.verbose = ctx.obj["VERBOSE"]
    api.app.state.root = pathlib.Path.cwd().resolve()

    tunnel_proc = None
    if tunnel:
        click.echo(f"[ Lemming ] Starting local server on port {port}...")
        click.echo(f"[ Lemming ] Initiating public tunnel via {tunnel.capitalize()}...")

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
            "[ Lemming ] Your Lemming instance is being exposed to the public internet."
        )
        click.echo(
            "[ Lemming ] Token-based authentication has been automatically enabled."
        )
        click.echo("[ Lemming ] ")
        click.echo("[ Lemming ] 🌐 Share this exact, secure link with the remote user:")
        click.echo(f"[ Lemming ] 👉 {public_url}?token={token}")
        click.echo("")
    else:
        click.echo(f"Launching Lemming UI at http://{host}:{port}")

    # Default timeout to 8h for tunnel mode, 0 (disabled) for local mode.
    timeout_str = timeout if timeout is not None else ("8h" if tunnel else "0")
    timeout_seconds = parse_timeout(timeout_str)

    if timeout_seconds > 0:
        click.echo(
            f"[ Lemming ] The server will automatically shut down in {timeout_str}."
        )

        def monitor():
            time.sleep(timeout_seconds)
            click.echo("\n[ Lemming ] Timeout reached. Waiting for tasks to finish...")
            if tunnel_proc:
                tunnel_proc.stop()

            tasks_file = api.app.state.tasks_file
            while True:
                project_data = tasks.get_project_data(tasks_file)
                if not project_data.loop_running:
                    break
                time.sleep(5)

            click.echo("[ Lemming ] All tasks finished. Exiting.")
            os._exit(0)

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()

    if tunnel:
        click.echo(
            "[ Lemming ] Press Ctrl+C to manually close the tunnel and shut down the server."
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
