"""CLI commands for installing the packaged Agent Skill.

Installing the CLI is not enough: agents discover skills by scanning
particular directories, and those directories are per-tool. `~/.agents/skills`
is the emerging cross-tool location, but several tools read only their own. So
rather than guess, `install` writes to the shared location and reports every
tool directory it can see, with the command to cover those too.
"""

import pathlib
import shutil
from importlib import resources

import click

from .main import cli

SKILL_NAME = "lemming"

# The tool-agnostic location, read by several agents and a reasonable default
# even where a tool also keeps its own directory.
SHARED_DIR = pathlib.Path(".agents") / "skills"

# Per-tool skills directories, keyed by the marker showing the tool is here.
TOOL_DIRS: dict[str, tuple[pathlib.Path, pathlib.Path]] = {
    "Claude Code": (
        pathlib.Path(".claude"),
        pathlib.Path(".claude") / "skills",
    ),
    "Gemini CLI": (pathlib.Path(".gemini"), pathlib.Path(".gemini") / "skills"),
    "Antigravity": (
        pathlib.Path(".gemini"),
        pathlib.Path(".gemini") / "config" / "skills",
    ),
    "Cursor": (pathlib.Path(".cursor"), pathlib.Path(".cursor") / "skills"),
}


def packaged_skill() -> pathlib.Path:
    """Locates SKILL.md, whether installed as a wheel or run from a checkout.

    It is authored at the repository root, where it stays visible, and mapped
    into the package at build time. Both locations have to work: the wheel
    path for real installs, the checkout path when running from source.

    Returns:
        Path to the packaged skill manifest.

    Raises:
        click.ClickException: If the manifest is in neither location.
    """
    packaged = pathlib.Path(
        str(resources.files("lemming") / "skills" / SKILL_NAME / "SKILL.md")
    )
    if packaged.is_file():
        return packaged

    checkout = pathlib.Path(__file__).resolve().parents[3] / "SKILL.md"
    if checkout.is_file():
        return checkout

    raise click.ClickException(
        f"SKILL.md not found at {packaged} or {checkout}"
    )


def detected_tools(home: pathlib.Path) -> dict[str, pathlib.Path]:
    """Returns skills directories for the agent tools present on this machine.

    Args:
        home: The user's home directory.

    Returns:
        Mapping of tool name to its skills directory, for tools found.
    """
    return {
        name: home / skills
        for name, (marker, skills) in TOOL_DIRS.items()
        if (home / marker).is_dir()
    }


def _primary_target(
    destination: pathlib.Path | None, home: pathlib.Path
) -> pathlib.Path:
    """Returns the one location acted on when no sweep was requested.

    A repository-scoped install is just ``--to .agents/skills``, so it needs
    no flag of its own.
    """
    if destination is not None:
        return destination / SKILL_NAME
    return home / SHARED_DIR / SKILL_NAME


def _is_our_skill(target: pathlib.Path) -> bool:
    """Returns whether a directory holds the skill this command installs.

    A broken symlink counts: a link into an environment that was since pruned
    is still wreckage this command created, and refusing to clean exactly that
    up would be perverse.
    """
    manifest = target / "SKILL.md"
    if manifest.is_symlink() and not manifest.exists():
        return True
    if not manifest.is_file():
        return False

    # Unreadable or not text: not something written here, and certainly not
    # something to delete on the strength of a guess.
    try:
        return f"name: {SKILL_NAME}" in manifest.read_text(errors="replace")
    except OSError:
        return False


def _remove(target: pathlib.Path) -> None:
    """Removes an installed skill, whether it is a link, file, or directory."""
    if target.is_symlink() or target.is_file():
        target.unlink()
    else:
        shutil.rmtree(target)


def _place(
    source: pathlib.Path,
    target: pathlib.Path,
    *,
    link: bool,
    force: bool,
) -> str:
    """Places the manifest into a skill directory of its own.

    Copying is the default because a link points into the environment this
    CLI was installed into; run under ``uvx`` that is a prunable cache, so the
    skill would work today and vanish later. A copy costs a stale skill after
    an upgrade, which re-running with --force fixes.

    Args:
        source: The packaged manifest to install.
        target: Directory named for the skill, as the spec requires.
        link: Symlink instead of copying.
        force: Replace an existing installation of this same skill.

    Returns:
        A line describing what was done.

    Raises:
        click.ClickException: If the target exists and must not be replaced.
    """
    if target.exists() or target.is_symlink():
        # --force licenses replacing *this* skill, never whatever happens to
        # sit at a mistyped --to. Deleting a tree on the strength of its name
        # is not something to do.
        if not _is_our_skill(target):
            raise click.ClickException(
                f"{target} exists and does not contain the {SKILL_NAME} "
                "skill; refusing to replace it. Remove it by hand if that is "
                "really intended."
            )
        if not force:
            raise click.ClickException(
                f"{target} already exists; pass --force to replace it"
            )
        _remove(target)

    target.mkdir(parents=True, exist_ok=True)
    manifest = target / "SKILL.md"

    if link:
        try:
            manifest.symlink_to(source)
        except OSError as e:
            # Windows needs Developer Mode or admin rights for symlinks. A
            # copy is a worse answer than a link, and a much better one than
            # a traceback.
            click.echo(f"# symlink failed ({e}); copying instead", err=True)
        else:
            return f"linked  {manifest} -> {source}"

    shutil.copy2(source, manifest)
    return f"copied  {target}"


@cli.group("skill")
def skill_group():
    """Install the packaged Agent Skill so agents can discover Lemming."""


@skill_group.command("install", short_help="Install the Agent Skill")
@click.option(
    "--to",
    "destination",
    type=click.Path(file_okay=False, path_type=pathlib.Path),
    help=(
        f"A skills directory to act on. The skill lives in a '{SKILL_NAME}' "
        "subdirectory of it, as the spec requires."
    ),
)
@click.option(
    "--all",
    "every",
    is_flag=True,
    help="Also install into every detected tool's own skills directory.",
)
@click.option(
    "--link",
    is_flag=True,
    help=(
        "Symlink instead of copying, so package upgrades take effect "
        "immediately. Only safe for a durable install: a link into a uvx "
        "environment dies on 'uv cache prune'."
    ),
)
@click.option("--force", is_flag=True, help="Replace an existing installation.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would happen without touching the filesystem.",
)
def install_command(
    destination: pathlib.Path | None,
    every: bool,
    link: bool,
    force: bool,
    dry_run: bool,
) -> None:
    """Installs the Agent Skill into an agent's skills directory.

    With no options this writes to ~/.agents/skills, the cross-tool location,
    and reports any tool-specific directories found so you can add them with
    --all.

    Examples:
      lemming skill install
      lemming skill install --all
      lemming skill install --to ~/.claude/skills
      lemming skill install --to .agents/skills
    """
    source = packaged_skill()
    home = pathlib.Path.home()
    targets = [_primary_target(destination, home)]

    found = detected_tools(home)
    if every:
        targets += [directory / SKILL_NAME for directory in found.values()]

    for target in targets:
        if dry_run:
            # Predict the real run's refusals too: a preview promising a
            # success the command would decline is worse than no preview.
            if (target.exists() or target.is_symlink()) and not _is_our_skill(
                target
            ):
                click.echo(
                    f"would REFUSE   {target}: not the {SKILL_NAME} skill"
                )
            elif target.exists() and not force:
                click.echo(f"would REFUSE   {target}: exists, needs --force")
            else:
                click.echo(f"would install  {target}")
            continue
        try:
            click.echo(_place(source, target, link=link, force=force))
        except OSError as e:
            raise click.ClickException(
                f"could not install into {target}: {e.strerror or e}"
            ) from None

    # Naming the alternatives beats silently installing into someone's home
    # directory four times over.
    if found and not every and destination is None:
        click.echo()
        click.echo("# also found tool-specific skills directories:")
        for name, directory in found.items():
            click.echo(f"#   {name}: {directory / SKILL_NAME}")
        click.echo("# install into those too with: lemming skill install --all")


@skill_group.command("uninstall", short_help="Remove the Agent Skill")
@click.option(
    "--to",
    "destination",
    type=click.Path(file_okay=False, path_type=pathlib.Path),
    help="A skills directory to act on.",
)
@click.option(
    "--all",
    "every",
    is_flag=True,
    help=(
        "Sweep every known location, whether or not the tool is still "
        "installed. Missing ones are skipped quietly."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would happen without touching the filesystem.",
)
def uninstall_command(
    destination: pathlib.Path | None, every: bool, dry_run: bool
) -> None:
    """Removes the Agent Skill from an agent's skills directory.

    Only ever removes a directory that actually holds this skill, so pointing
    --to somewhere unexpected fails rather than deleting someone's work.
    """
    home = pathlib.Path.home()
    targets = [_primary_target(destination, home)]

    # Sweeping is for cleanup, so it covers every known location rather than
    # only the tools still present: an uninstalled tool can leave a skill
    # behind, and that is exactly what needs removing.
    if every:
        targets += [
            home / skills / SKILL_NAME for _, skills in TOOL_DIRS.values()
        ]

    removed = 0
    for target in dict.fromkeys(targets):
        if not target.exists() and not target.is_symlink():
            continue

        if not _is_our_skill(target):
            raise click.ClickException(
                f"{target} does not contain the {SKILL_NAME} skill; refusing "
                "to delete it. Remove it by hand if that is really intended."
            )

        if dry_run:
            click.echo(f"would remove  {target}")
        else:
            try:
                _remove(target)
            except OSError as e:
                raise click.ClickException(
                    f"could not remove {target}: {e.strerror or e}"
                ) from None
            click.echo(f"removed  {target}")
        removed += 1

    if not removed:
        click.echo("nothing to remove")
