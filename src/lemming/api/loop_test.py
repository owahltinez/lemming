import pathlib
import sys
from unittest import mock

from lemming import models, persistence
from lemming.api import loop


def test_start_loop_if_needed_already_running():
    app_state = mock.Mock()
    app_state.disable_auto_start = False
    tasks_file = pathlib.Path("/tmp/tasks.yml")

    with mock.patch("lemming.tasks.is_loop_running", return_value=True):
        with mock.patch("subprocess.Popen") as mock_popen:
            loop.start_loop_if_needed(app_state, tasks_file)
            mock_popen.assert_not_called()


def test_start_loop_if_needed_disabled():
    app_state = mock.Mock()
    app_state.disable_auto_start = True
    tasks_file = pathlib.Path("/tmp/tasks.yml")

    with mock.patch("lemming.tasks.is_loop_running", return_value=False):
        with mock.patch("subprocess.Popen") as mock_popen:
            loop.start_loop_if_needed(app_state, tasks_file)
            mock_popen.assert_not_called()


def test_start_loop_if_needed_starts_process(tmp_path):
    app_state = mock.Mock()
    app_state.disable_auto_start = False
    app_state.verbose = True
    tasks_file = tmp_path / "tasks.yml"
    # Scaffold tasks file
    persistence.save_tasks(tasks_file, models.Roadmap())
    cwd = pathlib.Path("/tmp/cwd")

    with mock.patch("lemming.tasks.is_loop_running", return_value=False):
        with mock.patch("subprocess.Popen") as mock_popen:
            loop.start_loop_if_needed(app_state, tasks_file, cwd=cwd)

            expected_cmd = [
                sys.executable,
                "-m",
                "lemming.main",
                "--verbose",
                "--tasks-file",
                str(tasks_file),
                "run",
            ]
            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            assert args[0] == expected_cmd
            assert kwargs["start_new_session"] is True
            assert kwargs["cwd"] == cwd
            assert "PATH" in kwargs["env"]
