"""Resolution of the file scope a review agent should look at.

A review needs to be told what to read. Paths are already an instruction an
agent can act on, so they pass through untouched; only a revision range
needs translating, because reading one means running exactly the right git
command and that is not worth delegating.
"""

import logging
import pathlib
import subprocess

logger = logging.getLogger(__name__)

# Most entries worth rendering into a prompt. Past this the list stops
# being useful context and the range that produced it is the better
# instruction, since the agent can take the diff itself.
MAX_SCOPE_ENTRIES = 100

# Scope used when there is no git repository to diff against.
WHOLE_TREE = "."

# How git spells a revision range, and so how one is told from a path.
RANGE_MARKER = ".."


class ScopeError(ValueError):
    """Raised when a requested scope cannot be resolved."""


def _git(working_dir: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Runs a git command in a directory without raising on failure.

    Args:
        working_dir: Directory to run the command in.
        *args: Arguments following the git executable.

    Returns:
        The completed process, whatever its exit code.
    """
    return subprocess.run(
        ["git", *args],
        cwd=working_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def _lines(result: subprocess.CompletedProcess) -> list[str]:
    """Returns the non-empty output lines of a completed git command."""
    return [line for line in result.stdout.splitlines() if line.strip()]


def _in_git_repo(working_dir: pathlib.Path) -> bool:
    """Returns whether the directory sits inside a git work tree."""
    result = _git(working_dir, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def _uncommitted_paths(working_dir: pathlib.Path) -> list[str]:
    """Returns tracked edits and new files, excluding what git ignores.

    Args:
        working_dir: Root of the repository to inspect.

    Returns:
        Deduplicated paths, in a stable order.
    """
    tracked = _git(working_dir, "diff", "--name-only", "HEAD")
    untracked = _git(working_dir, "ls-files", "--others", "--exclude-standard")
    return sorted(set(_lines(tracked) + _lines(untracked)))


def _range_paths(spec: str, working_dir: pathlib.Path) -> list[str]:
    """Returns the files a revision range touches.

    Args:
        spec: A git revision or revision range.
        working_dir: Root of the repository to inspect.

    Returns:
        The changed paths, in git's order.

    Raises:
        ScopeError: If git cannot read the range.
    """
    result = _git(working_dir, "diff", "--name-only", spec)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ScopeError(
            f"Could not resolve '{spec}' as a path or a revision range"
            + (f": {detail[-1]}" if detail else ".")
        )
    return _lines(result)


def resolve_scope(
    values: tuple[str, ...] | list[str],
    working_dir: pathlib.Path,
    max_entries: int = MAX_SCOPE_ENTRIES,
) -> list[str]:
    """Returns the entries a review should be pointed at.

    Args:
        values: Requested scopes: paths, or git revision ranges. Empty
            means the default, which is whatever is uncommitted.
        working_dir: Directory the review runs in.
        max_entries: Most paths to expand a range into before falling back
            to naming the range itself.

    Returns:
        Paths relative to working_dir, or a revision range when expanding
        it would produce a list too long to be useful. Empty when there is
        nothing to review.

    Raises:
        ScopeError: If a requested scope is neither a path nor a range.
    """
    if not values:
        # Without git there is no notion of a change, so the only honest
        # default is the tree the caller is standing in.
        if not _in_git_repo(working_dir):
            return [WHOLE_TREE]
        return _uncommitted_paths(working_dir)

    resolved: list[str] = []
    for value in values:
        # Only a range is translated, and ".." is how git spells one. A
        # path is left alone without checking that it exists: requiring
        # that would reject globs, and an agent reports a missing path
        # more usefully than a guess here would.
        if RANGE_MARKER not in value:
            resolved.append(value)
            continue

        paths = _range_paths(value, working_dir)
        if len(paths) > max_entries:
            logger.info(
                "Scope '%s' covers %d files; naming the range instead.",
                value,
                len(paths),
            )
            resolved.append(value)
            continue
        resolved.extend(paths)

    return resolved


def describe(entries: list[str]) -> str:
    """Renders resolved scope entries for a review prompt.

    Args:
        entries: Resolved scope entries.

    Returns:
        A block naming what to review, or saying there is nothing to.
    """
    if not entries:
        return "There are no changes to review."

    listed = "\n".join(f"- {entry}" for entry in entries)
    return f"Review the following, relative to the project root:\n{listed}"
