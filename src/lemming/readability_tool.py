import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import click
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# Global logger for the module
logger = logging.getLogger("readability")


def get_guides_dir() -> Path:
    """
    Get the directory where style guides are cached.
    Defaults to 'guides/' in the same directory as this script,
    but can be overridden by the READABILITY_CACHE environment variable.
    """
    cache_env = os.environ.get("READABILITY_CACHE")
    if cache_env:
        return Path(cache_env)

    return Path(__file__).parent / "guides"


# Mapping of languages to their Google Style Guide file paths
LANGUAGE_MAP: dict[str, str] = {
    "python": "pyguide.md",
    "shell": "shellguide.md",
    "objc": "objcguide.md",
    "objective-c": "objcguide.md",
    "r": "Rguide.md",
    "csharp": "csharp-style.md",
    "c#": "csharp-style.md",
    "docguide": "docguide/style.md",
    "markdown": "docguide/style.md",
    "go": "go/guide.md",
    "cpp": "cppguide.html",
    "c++": "cppguide.html",
    "java": "javaguide.html",
    "js": "jsguide.html",
    "javascript": "jsguide.html",
    "ts": "tsguide.html",
    "typescript": "tsguide.html",
    "html": "htmlcssguide.html",
    "css": "htmlcssguide.html",
    "json": "jsoncstyleguide.xml",
    "vim": "vimscriptguide.xml",
}

BASE_URL = "https://google.github.io/styleguide/"


def fetch_guide_content(url: str) -> str:
    """
    Fetch raw content from the specified URL.
    """
    logger.info(f"Fetching style guide from {url}")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch content from {url}: {e}")
        raise click.ClickException(f"Failed to fetch style guide from {url}: {e}")

    return response.text


def convert_to_markdown(content: str, filename: str) -> str:
    """
    Convert the raw content to markdown based on file extension.
    """
    logger.debug(f"Converting content for {filename}")

    # Handle Markdown files directly
    if filename.endswith(".md"):
        return content

    # Handle HTML files by converting them to Markdown
    if filename.endswith(".html"):
        return str(md(content, heading_style="ATX"))

    # Handle XML files (used for Vim script guide)
    if filename.endswith(".xml"):
        soup = BeautifulSoup(content, "html.parser")
        return soup.get_text()

    # Fallback to returning raw content
    return content


def get_local_path(filename: str) -> Path:
    """
    Get the local path for a given style guide filename.
    """
    # Use the base filename and change extension to .md for uniform storage
    base_name = Path(filename).stem
    return get_guides_dir() / f"{base_name}.md"


def fetch_guide(language: str, remote: bool = False) -> str:
    """
    Orchestrate fetching and converting the style guide for a given language.
    """
    filename = LANGUAGE_MAP.get(language.lower())
    if not filename:
        error_msg = f"Language '{language}' is not supported."
        logger.warning(error_msg)
        raise click.UsageError(
            f"{error_msg} Supported languages: {', '.join(sorted(LANGUAGE_MAP.keys()))}"
        )

    local_path = get_local_path(filename)

    # Check for local file first if remote is not forced
    if not remote and local_path.exists():
        logger.info(f"Reading style guide from local file: {local_path}")
        return local_path.read_text(encoding="utf-8")

    # Fetch and convert remote content
    url = f"{BASE_URL}{filename}"
    content = fetch_guide_content(url)
    markdown_content = convert_to_markdown(content, filename)

    # Ensure cache directory exists and save content
    guides_dir = get_guides_dir()
    guides_dir.mkdir(parents=True, exist_ok=True)
    local_path.write_text(markdown_content, encoding="utf-8")
    logger.debug(f"Cached style guide locally: {local_path}")

    return markdown_content


def _setup_logging(verbose: bool = False) -> None:
    """
    Configure logging with a consistent format.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
        force=True,  # Override any existing configuration
    )
    logger.setLevel(level)


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def cli(ctx: click.Context, verbose: bool) -> None:
    """
    Pulls the latest Google style guide for the selected LANGUAGE in markdown format.
    """
    _setup_logging(verbose)


@cli.command()
@click.argument("language")
@click.option(
    "--output", "-o", type=click.Path(), help="Path to save the style guide markdown."
)
@click.option("--remote", "-r", is_flag=True, help="Force fetching from the web.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def fetch(language: str, output: str | None, remote: bool, verbose: bool) -> None:
    """
    Fetch the style guide for a specific LANGUAGE.
    """
    if verbose:
        _setup_logging(True)

    logger.info(f"Processing style guide for: {language}")

    try:
        markdown_content = fetch_guide(language, remote=remote)

        if output:
            Path(output).write_text(markdown_content, encoding="utf-8")
            logger.info(f"Style guide saved to {output}")
        else:
            click.echo(markdown_content)

    except (click.ClickException, click.UsageError) as e:
        logger.error(f"Execution failed: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def sync(verbose: bool) -> None:
    """
    Synchronize all supported style guides from the web to local storage.
    """
    if verbose:
        _setup_logging(True)

    logger.info("Synchronizing all style guides...")

    # Get unique filenames to avoid redundant downloads
    filenames = set(LANGUAGE_MAP.values())
    success_count = 0
    failure_count = 0

    for filename in sorted(filenames):
        logger.info(f"Syncing {filename}...")
        try:
            url = f"{BASE_URL}{filename}"
            content = fetch_guide_content(url)
            markdown_content = convert_to_markdown(content, filename)
            local_path = get_local_path(filename)

            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(markdown_content, encoding="utf-8")

            logger.info(f"Successfully synced {filename} to {local_path}")
            success_count += 1
        except (requests.RequestException, OSError, click.ClickException) as e:
            logger.error(f"Failed to sync {filename}: {e}")
            failure_count += 1

    logger.info(f"Sync complete. Successes: {success_count}, Failures: {failure_count}")


@cli.command()
def languages() -> None:
    """
    List all supported languages and their aliases.
    """
    # Group languages by their target guide
    guides: dict[str, list[str]] = {}
    for lang, filename in LANGUAGE_MAP.items():
        if filename not in guides:
            guides[filename] = []
        guides[filename].append(lang)

    click.echo("Supported languages and their aliases:")
    for filename in sorted(guides.keys()):
        aliases = sorted(guides[filename])
        click.echo(f"  - {', '.join(aliases)}")


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option("--fix", is_flag=True, help="Automatically fix issues if possible.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def check(paths: Sequence[str], fix: bool, verbose: bool) -> None:
    """
    Run relevant formatters and linters for given paths.
    """
    if verbose:
        _setup_logging(True)

    # Resolve project root once for trigger file checking
    project_root = Path.cwd()

    # Process each provided path independently
    for path_str in paths:
        _check_path(Path(path_str), project_root, fix=fix)


def _check_path(path: Path, project_root: Path, fix: bool = False) -> None:
    """
    Apply relevant tools to a single path.
    """
    logger.info(f"Checking path: {path}")

    # Iterate through all supported tool definitions
    for tool in _get_tool_definitions(path):
        if _should_run_tool(tool, path, project_root):
            _run_tool(tool["name"], tool, logger, fix=fix)


def _should_run_tool(tool: dict[str, Any], path: Path, project_root: Path) -> bool:
    """
    Determine if a tool should be run on the given path based on triggers and extensions.
    """
    # Check if any trigger files (like pyproject.toml) exist in the project root
    has_trigger = any((project_root / t).exists() for t in tool["trigger"])

    # For files, also check if the extension matches one of the supported ones
    if path.is_file():
        return has_trigger and path.suffix in tool["extensions"]

    # For directories, the existence of a trigger file is sufficient
    return has_trigger


def _get_tool_definitions(path: Path) -> list[dict[str, Any]]:
    """
    Define supported tools and their associated triggers, extensions, and commands.
    """
    path_str = str(path)

    return [
        {
            "name": "ruff",
            "check": ["ruff", "check", path_str],
            "check_format": ["ruff", "format", "--check", path_str],
            "fix": ["ruff", "check", "--fix", path_str],
            "format": ["ruff", "format", path_str],
            "trigger": ["pyproject.toml", "ruff.toml", ".ruff.toml"],
            "extensions": [".py"],
        },
        {
            "name": "biome",
            "check": ["npx", "biome", "lint", path_str],
            "check_format": ["npx", "biome", "format", path_str],
            "fix": ["npx", "biome", "lint", "--write", path_str],
            "format": ["npx", "biome", "format", "--write", path_str],
            "trigger": ["biome.json", "biome.jsonc"],
            "extensions": [
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".json",
                ".jsonc",
                ".css",
                ".html",
            ],
        },
        {
            "name": "prettier",
            "check_format": ["npx", "prettier", "--check", path_str],
            "format": ["npx", "prettier", "--write", path_str],
            "trigger": [
                ".prettierrc",
                ".prettierrc.json",
                ".prettierrc.yml",
                ".prettierrc.yaml",
                ".prettierrc.js",
                "prettier.config.js",
                "prettier.config.cjs",
            ],
            "extensions": [
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".json",
                ".css",
                ".scss",
                ".html",
                ".md",
                ".yml",
                ".yaml",
            ],
        },
        {
            "name": "go fmt",
            "check_format": ["gofmt", "-l", path_str],
            "format": ["go", "fmt", path_str],
            "trigger": ["go.mod"],
            "extensions": [".go"],
        },
    ]


def _run_tool(
    tool_name: str,
    tool_config: dict[str, Any],
    logger: logging.Logger,
    fix: bool = False,
) -> None:
    """
    Orchestrate the execution of a specific formatting or linting tool.
    """
    # Identify the primary command to check for executable availability
    cmd = (
        tool_config.get("format")
        or tool_config.get("check")
        or tool_config.get("fix")
        or tool_config.get("check_format")
    )
    if not cmd:
        return

    executable = str(cmd[0])
    if not shutil.which(executable):
        logger.debug(f"Tool {tool_name} ({executable}) not found in PATH, skipping.")
        return

    logger.info(f"Running {tool_name}...")
    try:
        if fix:
            # 1. Run formatters (if available) - these are expected to modify files
            if "format" in tool_config:
                _execute_tool_command(tool_config["format"], logger)

            # 2. Run fixers (if available) - these apply automatic linting fixes
            if "fix" in tool_config:
                _execute_tool_command(tool_config["fix"], logger)
        else:
            # 1. Run check_format (if available) - check-only
            if "check_format" in tool_config:
                logger.debug(f"Executing: {' '.join(tool_config['check_format'])}")
                result = subprocess.run(
                    tool_config["check_format"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0 or (
                    tool_name == "go fmt" and result.stdout.strip()
                ):
                    click.echo(
                        f"--- {tool_name} formatting findings ---\n{result.stdout}\n{result.stderr}"
                    )

        # 3. Run checks and report findings - these provide feedback to the user
        if "check" in tool_config:
            logger.debug(f"Executing: {' '.join(tool_config['check'])}")
            result = subprocess.run(
                tool_config["check"], capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                click.echo(
                    f"--- {tool_name} findings ---\n{result.stdout}\n{result.stderr}"
                )

    except subprocess.CalledProcessError as e:
        logger.warning(f"{tool_name} failed with exit code {e.returncode}")
        if e.stdout:
            logger.debug(f"STDOUT: {e.stdout}")
        if e.stderr:
            logger.debug(f"STDERR: {e.stderr}")
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Unexpected error while running {tool_name}: {e}")


def _execute_tool_command(cmd: list[str], logger: logging.Logger) -> None:
    """
    Execute a tool command and raise CalledProcessError if it returns a non-zero exit code.
    """
    logger.debug(f"Executing: {' '.join(cmd)}")
    subprocess.run(cmd, capture_output=True, check=True)


# Main entry point for the CLI script
def main():
    cli()


if __name__ == "__main__":
    main()
