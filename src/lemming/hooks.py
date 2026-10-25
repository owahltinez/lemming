"""Discovery and management of orchestrator hook files.

Hook execution lives in the orchestrator module; this module owns layered
discovery, priorities, and which hooks are enabled for a project or task.
"""

import dataclasses
import pathlib
import re
import typing

from . import paths, persistence

# Hook files follow a udev-style naming convention: "NN-name.md" where the
# numeric prefix orders execution and the remainder is the hook's logical
# name. Unprefixed files default to DEFAULT_HOOK_PRIORITY. Hooks at or above
# FAILURE_HOOK_PRIORITY also run when a task fails.
DEFAULT_HOOK_PRIORITY = 50
FAILURE_HOOK_PRIORITY = 90

# Requests every review rather than making the caller name each one.
ALL_REVIEWS = "*"

_HOOK_STEM_RE = re.compile(r"^(\d+)-(.+)$")


@dataclasses.dataclass
class HookInfo:
    """A hook resolved to a single file across the discovery layers."""

    name: str
    priority: int
    path: pathlib.Path
    source: str  # "built-in", "global", or "local"
    masked: bool  # True when the winning file is empty (hook disabled)


def parse_hook_stem(stem: str) -> tuple[int, str]:
    """Splits a hook filename stem into (priority, logical name)."""
    match = _HOOK_STEM_RE.match(stem)
    if match:
        return int(match.group(1)), match.group(2)
    return DEFAULT_HOOK_PRIORITY, stem


def get_local_hooks_dir(tasks_file: pathlib.Path) -> pathlib.Path:
    """Returns the project-local hooks directory for a tasks file."""
    return paths.get_working_dir(tasks_file) / ".lemming" / "hooks"


def get_builtin_hooks_dir() -> pathlib.Path:
    """Returns the directory of hook prompts bundled with the package."""
    return pathlib.Path(__file__).parent / "prompts" / "hooks"


def resolve_hooks(
    tasks_file: pathlib.Path | None = None,
) -> list[HookInfo]:
    """Resolves hooks across the built-in, global, and project layers.

    For each logical name, the file in the highest-precedence layer wins
    (local > global > built-in), and the winning filename determines the
    execution priority. An empty file masks (disables) the hook.

    Args:
        tasks_file: Optional path to the tasks file to look for local hooks.

    Returns:
        Resolved hooks sorted by (priority, name).
    """
    layers = [
        ("built-in", get_builtin_hooks_dir()),
        ("global", paths.get_global_hooks_dir()),
    ]
    if tasks_file:
        layers.append(("local", get_local_hooks_dir(tasks_file)))

    resolved: dict[str, HookInfo] = {}
    for source, directory in layers:
        if not directory.exists():
            continue

        # Iterate in sorted order so duplicate logical names within one
        # layer resolve deterministically (lexicographically last wins)
        for f in sorted(directory.glob("*.md")):
            # Skip unreadable entries such as dangling symlinks or files
            # that are not valid UTF-8
            try:
                masked = not f.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue

            priority, name = parse_hook_stem(f.stem)
            resolved[name] = HookInfo(
                name=name,
                priority=priority,
                path=f,
                source=source,
                masked=masked,
            )

    return sorted(resolved.values(), key=lambda h: (h.priority, h.name))


def list_hooks(tasks_file: pathlib.Path | None = None) -> list[str]:
    """Lists active (non-masked) hook names in execution order."""
    return [h.name for h in resolve_hooks(tasks_file) if not h.masked]


def get_hook_priority(name: str, tasks_file: pathlib.Path | None = None) -> int:
    """Returns the execution priority of a hook by logical name."""
    for hook in resolve_hooks(tasks_file):
        if hook.name == name:
            return hook.priority
    return DEFAULT_HOOK_PRIORITY


def select_hooks(
    tasks_file: pathlib.Path,
    config_hooks: list[str],
    task_hooks: list[str] | None,
) -> list[str]:
    """Returns the hooks active for a task, in execution order.

    The 9x band always comes from the project; a task's own list, when set,
    replaces the project's reviews. Masked and unknown names are skipped.

    Args:
        tasks_file: Path to the tasks file identifying the project.
        config_hooks: The project's enabled hooks.
        task_hooks: The task's reviews, or None for the project's.

    Returns:
        Active hook names sorted by (priority, name).
    """
    selected = []
    for hook in resolve_hooks(tasks_file):
        names = (
            config_hooks
            if task_hooks is None or hook.priority >= FAILURE_HOOK_PRIORITY
            else task_hooks
        )
        if not hook.masked and hook.name in names:
            selected.append(hook.name)
    return selected


def parse_reviews(
    values: typing.Iterable[str], tasks_file: pathlib.Path | None = None
) -> list[str]:
    """Parses requested review hooks into names in execution order.

    Args:
        values: Requested reviews, each possibly comma-separated. The value
            "*" stands for every review.
        tasks_file: Optional project whose hooks to resolve; without it only
            the built-in and global layers are considered.

    Returns:
        Review names ordered by hook priority.

    Raises:
        ValueError: If a name is unknown or in the orchestration band.
    """
    requested = [
        name.strip()
        for value in values
        for name in value.split(",")
        if name.strip()
    ]
    active = [h for h in resolve_hooks(tasks_file) if not h.masked]
    reviews = [h.name for h in active if h.priority < FAILURE_HOOK_PRIORITY]

    if ALL_REVIEWS in requested:
        return reviews

    for name in requested:
        if name in reviews:
            continue
        if any(h.name == name for h in active):
            raise ValueError(
                f"'{name}' orchestrates the roadmap and cannot be selected "
                "as a review."
            )
        raise ValueError(
            f"Unknown review '{name}'. Available: {', '.join(reviews)}."
        )
    return [name for name in reviews if name in requested]


def enable_hooks(names: list[str], tasks_file: pathlib.Path) -> dict[str, bool]:
    """Adds hooks to the project's enabled set.

    All names are validated before the config is written, so a bad name
    never results in a partial application.

    Args:
        names: Logical names of the hooks to enable.
        tasks_file: Path to the tasks file identifying the project.

    Returns:
        Mapping of each name to True if it was added, or False if the hook
        was already enabled.

    Raises:
        ValueError: If any hook is unknown or masked.
    """
    resolved = {h.name: h for h in resolve_hooks(tasks_file)}
    for name in names:
        hook = resolved.get(name)
        if hook is None:
            raise ValueError(f"Hook '{name}' not found.")
        if hook.masked:
            raise ValueError(
                f"Hook '{name}' is masked by {hook.path}; remove that file "
                "first."
            )

    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        results = {name: name not in data.config.hooks for name in names}
        data.config.hooks += [name for name, added in results.items() if added]
        persistence.save_tasks(tasks_file, data)
    return results


def disable_hooks(
    names: list[str], tasks_file: pathlib.Path
) -> dict[str, bool]:
    """Removes hooks from the project's enabled set.

    Args:
        names: Logical names of the hooks to disable.
        tasks_file: Path to the tasks file identifying the project.

    Returns:
        Mapping of each name to True if it was removed, or False if the hook
        was already disabled.

    Raises:
        ValueError: If any name is neither enabled nor a known hook.
    """
    known = {h.name for h in resolve_hooks(tasks_file)}
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        for name in names:
            if name not in data.config.hooks and name not in known:
                raise ValueError(f"Hook '{name}' not found.")

        results = {name: name in data.config.hooks for name in names}
        data.config.hooks = [h for h in data.config.hooks if h not in names]
        persistence.save_tasks(tasks_file, data)
    return results
