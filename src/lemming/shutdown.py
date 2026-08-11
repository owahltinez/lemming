"""Graceful shutdown coordination for the orchestrator loop."""

import signal
import threading
import types

# Requesting a drain must not disturb the running task, so it travels on a
# user signal rather than SIGTERM.
DRAIN_SIGNAL = signal.SIGUSR1

# Set when a drain is requested; the loop exits after the current task.
_drain_requested = threading.Event()


def drain_requested() -> bool:
    """Returns whether the loop should exit after the current task."""
    return _drain_requested.is_set()


def request_drain() -> None:
    """Marks the loop as draining."""
    _drain_requested.set()


def clear_drain() -> None:
    """Clears any pending drain request so a new loop starts undrained."""
    _drain_requested.clear()


def _handle_terminate(signum: int, frame: types.FrameType | None) -> None:
    """Turns SIGTERM into the interrupt the runner cleanup already handles."""
    raise KeyboardInterrupt


def _handle_drain(signum: int, frame: types.FrameType | None) -> None:
    """Records a drain request without interrupting the running task."""
    request_drain()


def install_handlers() -> None:
    """Installs the loop's shutdown signal handlers.

    SIGTERM raises KeyboardInterrupt so ``run_with_heartbeat`` kills the
    runner's process tree on the way out. Without it SIGTERM terminates the
    orchestrator immediately and the runner, started in its own session,
    survives as an orphan; once the task heartbeat goes stale a restarted
    loop reclaims the same task and two agents write to one directory.

    SIGHUP gets the same treatment, because a loop launched by another
    agent receives it when that parent dies; its default action would end
    the process outright and leave the same orphan behind.

    ``DRAIN_SIGNAL`` instead lets the current task finish and stops the loop
    before it claims another.
    """
    signal.signal(signal.SIGTERM, _handle_terminate)
    signal.signal(signal.SIGHUP, _handle_terminate)
    signal.signal(DRAIN_SIGNAL, _handle_drain)
