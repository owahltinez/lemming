"""Helpers for building and inspecting hermetic eval workspaces.

Every eval trial runs against a throwaway git repository seeded with a small
fixture project plus a tasks file. The baseline commit lets graders detect
any source drift caused by the agent under eval with plain git status.
"""

import pathlib
import subprocess

from .. import models, tasks

TASKS_FILE_NAME = "tasks.yml"

# Files owned by the eval machinery rather than the agent under eval. They
# are gitignored in fixtures so dirty_paths only reports agent-made changes.
WORKSPACE_IGNORES = (
    TASKS_FILE_NAME,
    ".lemming/",
    "*.log",
    "*.lock",
    "__pycache__/",
)


def _git(workspace: pathlib.Path, *args: str) -> str:
    """Runs a git command inside the workspace and returns its stdout."""
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return result.stdout


def init_repo(workspace: pathlib.Path, files: dict[str, str]) -> None:
    """Creates a git repository seeded with files and a baseline commit.

    Args:
        workspace: Directory to initialize (created if missing).
        files: Mapping of relative file paths to their contents.
    """
    # Seed the fixture files plus the ignore list for eval-owned state.
    workspace.mkdir(parents=True, exist_ok=True)
    for relative_path, content in files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    ignore_file = workspace / ".gitignore"
    ignore_file.write_text("\n".join(WORKSPACE_IGNORES) + "\n")

    # Commit the baseline with a local identity so no host config is needed.
    _git(workspace, "init", "--quiet", "--initial-branch=main")
    _git(workspace, "config", "user.email", "evals@lemming.invalid")
    _git(workspace, "config", "user.name", "Lemming Evals")
    _git(workspace, "add", "--all")
    _git(workspace, "commit", "--quiet", "--message", "Baseline fixture")


def tasks_file(workspace: pathlib.Path) -> pathlib.Path:
    """Returns the path of the tasks file inside a workspace."""
    return workspace / TASKS_FILE_NAME


def save_roadmap(workspace: pathlib.Path, roadmap: models.Roadmap) -> None:
    """Persists a roadmap to the workspace tasks file."""
    tasks.save_tasks(tasks_file(workspace), roadmap)


def load_roadmap(workspace: pathlib.Path) -> models.Roadmap:
    """Loads the roadmap from the workspace tasks file."""
    return tasks.load_tasks(tasks_file(workspace))


def dirty_paths(workspace: pathlib.Path) -> list[str]:
    """Returns workspace paths that changed since the baseline commit.

    Eval-owned files (tasks file, lemming state, logs) are gitignored at
    fixture creation, so any path reported here is agent-made source drift.

    Args:
        workspace: The workspace repository to inspect.

    Returns:
        Repo-relative paths of modified, added, or deleted files.
    """
    output = _git(workspace, "status", "--porcelain")
    return [line[3:].strip() for line in output.splitlines() if line.strip()]
