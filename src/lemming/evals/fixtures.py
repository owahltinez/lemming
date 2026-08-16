"""Helpers for building and inspecting hermetic eval workspaces.

Every eval trial runs against a throwaway git repository seeded with a small
fixture project plus a tasks file. The baseline commit lets graders detect
any source drift caused by the agent under eval with plain git status.
"""

import pathlib
import subprocess

from .. import models, tasks

TASKS_FILE_NAME = "tasks.yml"

# Fixture projects are kept as real files rather than as source in strings,
# so they can be read, linted and edited like the code they imitate. Some
# are deliberately dirty, which is why this repo's own tooling excludes the
# directory; the workspace they are copied into excludes nothing, so a
# grader holds them to exactly the standard it holds an agent's output to.
PROJECTS_DIR = pathlib.Path(__file__).parent / "projects"

# Files owned by the eval machinery or by the tools an agent runs, rather
# than by the agent under eval. They are gitignored in fixtures so the path
# helpers below only report agent-made changes. The tool caches matter
# because a scenario that grades restraint would otherwise read "the agent
# ran pytest" as "the agent left a stray directory behind".
WORKSPACE_IGNORES = (
    TASKS_FILE_NAME,
    ".lemming/",
    "*.log",
    "*.lock",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
)


def load_project(name: str) -> dict[str, str]:
    """Reads a fixture project into the mapping init_repo takes.

    Args:
        name: Project directory under PROJECTS_DIR, e.g. "roadmap/add-only".

    Returns:
        Paths relative to that directory, mapped to their contents.
    """
    root = PROJECTS_DIR / name
    files = sorted(path for path in root.rglob("*") if path.is_file())
    return {str(path.relative_to(root)): path.read_text() for path in files}


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


def _write_files(workspace: pathlib.Path, files: dict[str, str]) -> None:
    """Writes a mapping of relative paths to contents into the workspace."""
    for relative_path, content in files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def init_repo(
    workspace: pathlib.Path,
    files: dict[str, str],
    changes: dict[str, str] | None = None,
) -> None:
    """Creates a git repository seeded with files and a baseline commit.

    Args:
        workspace: Directory to initialize (created if missing).
        files: Mapping of relative file paths to their contents.
        changes: Edits a finished task made on top of the baseline. When
            given, they land in a second commit so the trial starts with a
            real diff to review; graders read it back with changed_paths.
    """
    # Seed the fixture files plus the ignore list for eval-owned state.
    workspace.mkdir(parents=True, exist_ok=True)
    _write_files(workspace, files)
    ignore_file = workspace / ".gitignore"
    ignore_file.write_text("\n".join(WORKSPACE_IGNORES) + "\n")

    # Commit the baseline with a local identity so no host config is needed.
    _git(workspace, "init", "--quiet", "--initial-branch=main")
    _git(workspace, "config", "user.email", "evals@lemming.invalid")
    _git(workspace, "config", "user.name", "Lemming Evals")
    _git(workspace, "add", "--all")
    _git(workspace, "commit", "--quiet", "--message", "Baseline fixture")

    # The finished task's work is a separate commit, so HEAD~1..HEAD is the
    # scope a review hook is supposed to look at and git status still shows
    # nothing but drift the agent under eval caused.
    if changes:
        _write_files(workspace, changes)
        _git(workspace, "add", "--all")
        _git(workspace, "commit", "--quiet", "--message", "Finished task")


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


def _baseline_commit(workspace: pathlib.Path) -> str:
    """Returns the fixture's baseline commit, i.e. the repository root."""
    output = _git(workspace, "rev-list", "--max-parents=0", "HEAD")
    return output.split()[0]


def changed_since_baseline(workspace: pathlib.Path) -> list[str]:
    """Returns every path that differs from the fixture as it was built.

    dirty_paths only sees the working tree, so an agent that commits its
    work leaves it empty and looks like an agent that did nothing. Task
    scenarios grade the code an agent wrote, which makes that difference
    the whole result, so the comparison is anchored to the baseline commit
    instead of to HEAD.

    Args:
        workspace: The workspace repository to inspect.

    Returns:
        Sorted repo-relative paths, covering committed edits, working-tree
        edits, and untracked files. Gitignored eval-owned files are
        excluded, exactly as in dirty_paths.
    """
    baseline = _baseline_commit(workspace)
    tracked = _git(workspace, "diff", "--name-only", baseline)
    untracked = _git(workspace, "ls-files", "--others", "--exclude-standard")
    lines = tracked.splitlines() + untracked.splitlines()
    return sorted({line.strip() for line in lines if line.strip()})


def added_lines_since_baseline(workspace: pathlib.Path) -> int:
    """Returns how many lines were added on top of the baseline commit.

    Size alone says nothing -- doing nothing scores a perfect zero -- so
    this is only meaningful next to a check that the work was actually
    done. Untracked files are not counted; a scenario reading this is
    expected to bound new files separately.

    Args:
        workspace: The workspace repository to inspect.

    Returns:
        Total added lines across tracked files, committed or not.
    """
    output = _git(workspace, "diff", "--numstat", _baseline_commit(workspace))
    counts = (line.split()[:1] for line in output.splitlines() if line.strip())
    # Binary files report "-" instead of a count and contribute nothing.
    return sum(int(count[0]) for count in counts if count[0].isdigit())


def changed_paths(workspace: pathlib.Path) -> list[str]:
    """Returns the paths the finished task's commit touched.

    This is the scope a review hook is expected to look at. Fixtures built
    without a ``changes`` mapping have no task commit and report nothing.

    Args:
        workspace: The workspace repository to inspect.

    Returns:
        Repo-relative paths changed by the task commit, or an empty list
        when the fixture has only its baseline commit.
    """
    revisions = _git(workspace, "rev-list", "--count", "HEAD").strip()
    if int(revisions) < 2:
        return []
    output = _git(workspace, "diff", "--name-only", "HEAD~1", "HEAD")
    return [line.strip() for line in output.splitlines() if line.strip()]
