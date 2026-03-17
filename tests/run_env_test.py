import sys
import click.testing

from lemming import main
from lemming import paths
from lemming import tasks


def test_run_with_env(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    runner = click.testing.CliRunner()

    # 1. Add a task
    runner.invoke(main.cli, ["--tasks-file", str(tasks_file), "add", "Task with env"])

    # 2. We want to verify that the environment variable is set when build_agent_command is called
    # or when the agent is run.
    # Since 'run' starts a loop, we can use a mock agent that prints its environment.

    agent_script = tmp_path / "mock_agent.py"

    # We'll run it once and expect it to finish.
    # But 'run' loop is infinite until all tasks are done.
    # Our mock agent doesn't mark the task as completed, so it would retry.
    # Let's make the mock agent mark it as completed.

    agent_script.write_text(
        f"""
import os
import sys
import subprocess
print(f'MOCK_ENV={{os.environ.get("MOCK_KEY")}}')
# Mark task as completed using the CLI
subprocess.run([sys.executable, "-m", "lemming.main", "--tasks-file", "{str(tasks_file)}", "outcome", sys.argv[-1], "done"], check=True)
subprocess.run([sys.executable, "-m", "lemming.main", "--tasks-file", "{str(tasks_file)}", "complete", sys.argv[-1]], check=True)
""",
        encoding="utf-8",
    )

    # Run lemming run with --env
    # We use --max-attempts 1 to avoid infinite loop if it fails
    # We use --agent and then the agent_args will be the script path
    runner.invoke(
        main.cli,
        [
            "--tasks-file",
            str(tasks_file),
            "run",
            "--agent",
            sys.executable,
            "--env",
            "MOCK_KEY=MOCK_VALUE",
            "--",
            str(agent_script),
        ],
    )

    # The output of the agent goes to the log file, not directly to click.echo unless verbose
    # Actually, it doesn't echo it at all if successful.

    # Let's check the log file
    data = tasks.load_tasks(tasks_file)
    task_id = data["tasks"][0]["id"]

    log_file = paths.get_log_file(tasks_file, task_id)
    assert log_file.exists()
    log_content = log_file.read_text(encoding="utf-8")
    assert "MOCK_ENV=MOCK_VALUE" in log_content
