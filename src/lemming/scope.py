"""Resolution of the file scope a review agent should look at.

A review needs to be told what to read. Paths and globs are already an
instruction an agent can act on, so they pass through untouched once each
is known to match something; only the default, uncommitted work, needs
translating into the files it covers.
"""

import glob
import pathlib
import re
import subprocess

# Scope used when there is no git repository to diff against.
WHOLE_TREE = "."

# Control characters and line separators; in a path, they would add lines
# to the review prompt that the agent reads as instructions.
_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


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
        errors="surrogateescape",
        check=False,
    )


def _names(result: subprocess.CompletedProcess) -> list[str]:
    """Returns the paths a git command listed with -z, unquoted."""
    return [name for name in result.stdout.split("\0") if name]


def _in_git_repo(working_dir: pathlib.Path) -> bool:
    """Returns whether the directory sits inside a git work tree."""
    result = _git(working_dir, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def _uncommitted_paths(working_dir: pathlib.Path) -> list[str]:
    """Returns tracked edits and new files, excluding what git ignores.

    Both commands report paths relative to working_dir and only under it,
    so every entry is readable from where the agent runs.

    Args:
        working_dir: Directory inside the repository to inspect.

    Returns:
        Deduplicated paths, in a stable order. Deleted files are left out,
        since there is nothing left of them to review.
    """
    # Without -z, git quotes unusual names, which then name no real file.
    tracked = _git(
        working_dir,
        "diff",
        "-z",
        "--name-only",
        "--relative",
        "--diff-filter=d",
        "HEAD",
    )
    untracked = _git(
        working_dir, "ls-files", "-z", "--others", "--exclude-standard"
    )
    return sorted(set(_names(tracked) + _names(untracked)))


def _matches(pattern: str, working_dir: pathlib.Path) -> bool:
    """Returns whether a path exists, or a glob matches at least one path.

    An existing path wins, so brackets in a real file name are not read as
    a pattern. An empty pattern matches nothing, although joining it to
    working_dir would name the directory itself.
    """
    if not pattern:
        return False
    try:
        if (working_dir / pattern).exists():
            return True
    except OSError:
        return False
    matches = glob.iglob(pattern, root_dir=working_dir, recursive=True)
    return next(matches, None) is not None


def resolve_scope(
    patterns: tuple[str, ...], working_dir: pathlib.Path
) -> list[str]:
    """Returns the entries a review should be pointed at.

    Args:
        patterns: Requested paths or globs. Empty means the default, which
            is whatever is uncommitted.
        working_dir: Directory the review runs in.

    Returns:
        The patterns as given, or the uncommitted paths relative to
        working_dir. Empty when there is nothing to review.

    Raises:
        ScopeError: If a pattern matches nothing, or an entry holds a
            control character.
    """
    missing = [p for p in patterns if not _matches(p, working_dir)]
    if missing:
        raise ScopeError(
            "Nothing matches " + ", ".join(repr(p) for p in missing) + "."
        )
    if patterns:
        entries = list(patterns)
    elif not _in_git_repo(working_dir):
        # Without git there is no notion of a change, so the only honest
        # default is the tree the caller is standing in.
        entries = [WHOLE_TREE]
    else:
        entries = _uncommitted_paths(working_dir)

    unsafe = [entry for entry in entries if _UNSAFE_CHARS.search(entry)]
    if unsafe:
        raise ScopeError(
            "Refusing paths with control characters: "
            + ", ".join(repr(entry) for entry in unsafe)
        )
    return entries


def one_line(text: str) -> str:
    """Returns text as is, or escaped when it could break a prompt line."""
    return repr(text) if _UNSAFE_CHARS.search(text) else text


def describe(entries: list[str], working_dir: pathlib.Path) -> str:
    """Renders resolved scope entries for a review prompt.

    Args:
        entries: Resolved scope entries.
        working_dir: Directory the entries are relative to. It is named in
            full because some runners run their tools somewhere else.

    Returns:
        A block naming what to review, or saying there is nothing to. A
        path that could break a line, as one a task created might, is
        escaped onto its own line rather than refused, since a hook cannot
        stop the run.
    """
    if not entries:
        return "There are no changes to review."

    listed = "\n".join(f"- {one_line(entry)}" for entry in entries)
    return (
        f"ONLY the following, relative to {one_line(str(working_dir))}:\n"
        f"{listed}\n"
        "Do not touch any other file, and do not leave new ones behind: "
        "write scratch output outside the workspace, never into it."
    )
