"""Discovery and management of orchestrator hook files.

Hook execution lives in the orchestrator module; this module owns the
filesystem layer: layered discovery, priorities, and mask operations.
"""

import dataclasses
import pathlib
import re

from . import paths

# Hook files follow a udev-style naming convention: "NN-name.md" where the
# numeric prefix orders execution and the remainder is the hook's logical
# name. Unprefixed files default to DEFAULT_HOOK_PRIORITY. Hooks at or above
# FAILURE_HOOK_PRIORITY also run when a task fails.
DEFAULT_HOOK_PRIORITY = 50
FAILURE_HOOK_PRIORITY = 90

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


def disable_hooks(
    names: list[str], tasks_file: pathlib.Path
) -> dict[str, pathlib.Path | None]:
    """Disables hooks for the project by writing empty mask files.

    All names are validated before any file is written, so a bad name never
    results in a partial application. The name check against resolved hooks
    also guarantees that only known hook names reach the filesystem (no
    caller-controlled paths).

    Args:
        names: Logical names of the hooks to disable.
        tasks_file: Path to the tasks file identifying the project.

    Returns:
        Mapping of each name to the mask file created, or None if the hook
        was already disabled.

    Raises:
        ValueError: If any hook is unknown, or a non-empty project override
            exists (masking it would clobber the override).
    """
    resolved = {h.name: h for h in resolve_hooks(tasks_file)}

    # Validate every name before applying any changes
    for name in names:
        hook = resolved.get(name)
        if hook is None:
            raise ValueError(f"Hook '{name}' not found.")

        # Never clobber a project override; the user must remove it first
        if not hook.masked and hook.source == "local":
            raise ValueError(
                f"Hook '{name}' has a project override at {hook.path}; "
                "delete or rename that file instead."
            )

    local_dir = get_local_hooks_dir(tasks_file)
    results: dict[str, pathlib.Path | None] = {}
    for name in names:
        hook = resolved[name]
        if hook.masked:
            results[name] = None
            continue

        # Keep the hook's priority in the mask filename so listings still
        # report the priority it would run at if re-enabled
        local_dir.mkdir(parents=True, exist_ok=True)
        mask = local_dir / f"{hook.priority}-{name}.md"
        mask.write_text("", encoding="utf-8")
        results[name] = mask

    return results


def enable_hooks(names: list[str], tasks_file: pathlib.Path) -> dict[str, bool]:
    """Re-enables hooks by removing their project mask files.

    All names are validated before any file is removed, so a bad name never
    results in a partial application.

    Args:
        names: Logical names of the hooks to enable.
        tasks_file: Path to the tasks file identifying the project.

    Returns:
        Mapping of each name to True if a mask was removed, or False if the
        hook was already enabled.

    Raises:
        ValueError: If any hook is unknown, is masked outside the project,
            or a matching project file has content (an override, not a
            mask).
    """
    local_dir = get_local_hooks_dir(tasks_file)
    resolved = {h.name: h for h in resolve_hooks(tasks_file)}

    # Validate every name and plan the removals before applying any changes
    plan: dict[str, list[pathlib.Path]] = {}
    for name in names:
        # Find project-layer files matching the hook's logical name
        matches = []
        if local_dir.exists():
            matches = [
                f
                for f in local_dir.glob("*.md")
                if parse_hook_stem(f.stem)[1] == name
            ]

        if not matches:
            hook = resolved.get(name)
            if hook is None:
                raise ValueError(f"Hook '{name}' not found.")
            if hook.masked:
                raise ValueError(
                    f"Hook '{name}' is masked outside the project at "
                    f"{hook.path}; remove that file manually."
                )
            plan[name] = []
            continue

        # Only delete masks; a file with content is an override, not a
        # mask, and unreadable files are treated as overrides to be safe
        overrides = []
        for f in matches:
            try:
                is_mask = not f.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                is_mask = False
            if not is_mask:
                overrides.append(f)
        if overrides:
            raise ValueError(
                f"Hook '{name}' has a project override at {overrides[0]}; "
                "delete it manually if that is intended."
            )
        plan[name] = matches

    results: dict[str, bool] = {}
    for name, masks in plan.items():
        for f in masks:
            f.unlink()
        results[name] = bool(masks)

    return results
