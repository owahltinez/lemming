import logging
import os
import shutil
import subprocess
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning
import click
from markdownify import markdownify as md
import requests


# Suppress BeautifulSoup warning when parsing XML as HTML
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Configure logging with structured format
logger = logging.getLogger("readability")


def get_guides_dir() -> str:
    """Get the directory where style guides are cached.

    Defaults to 'guides/' in the same directory as this script, but can be
    overridden by the READABILITY_CACHE environment variable.

    Returns:
        The path to the guides directory.
    """
    return os.getenv("READABILITY_CACHE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "guides"
    )


# Mapping of languages to their Google Style Guide file paths
LANGUAGE_MAP = {
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


def get_guide_content(url: str) -> str:
    """Fetch raw content from the specified URL.

    Args:
        url: The URL to fetch content from.

    Returns:
        The raw text content from the URL.

    Raises:
        click.ClickException: If the HTTP request fails.
    """
    logger.info("Fetching style guide from %s", url)

    # Perform the HTTP GET request with a timeout
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch content from %s: %s", url, e)
        raise click.ClickException(f"Failed to fetch style guide from {url}: {e}")

    return response.text


def convert_to_markdown(content: str, filename: str) -> str:
    """Convert the raw content to markdown based on file extension.

    Args:
        content: The raw text content to convert.
        filename: The original filename to determine conversion logic.

    Returns:
        The converted markdown content.
    """
    logger.debug("Converting content for %s", filename)

    # Handle Markdown files directly
    if filename.endswith(".md"):
        return content

    # Strip XML prologue if present to avoid it leaking into the output
    if content.lstrip().startswith("<?xml"):
        content = content.split("?>", 1)[-1].lstrip()

    # Handle XML files (used for Vim script guide and JSON style guide)
    if filename.endswith(".xml"):
        soup = BeautifulSoup(content, "html.parser")

        # Add titles as headers
        guide = soup.find("guide")
        if guide and guide.get("title"):
            h1 = soup.new_tag("h1")
            h1.string = guide.get("title")
            guide.insert(0, h1)

        for category in soup.find_all("category"):
            if category.get("title"):
                h2 = soup.new_tag("h2")
                h2.string = category.get("title")
                category.insert(0, h2)

        for sp in soup.find_all("stylepoint"):
            if sp.get("title"):
                h3 = soup.new_tag("h3")
                h3.string = sp.get("title")
                sp.insert(0, h3)

        for summary in soup.find_all("summary"):
            summary.name = "p"
            # Wrap content in strong tags
            content_str = summary.decode_contents()
            summary.clear()
            strong = soup.new_tag("strong")
            strong.append(BeautifulSoup(content_str, "html.parser"))
            summary.append(strong)

        for snippet in soup.find_all(["code_snippet", "bad_code_snippet"]):
            is_bad = snippet.name == "bad_code_snippet"
            snippet.name = "pre"
            code = soup.new_tag("code")
            code.string = snippet.get_text()
            snippet.clear()
            if is_bad:
                p = soup.new_tag("p")
                strong = soup.new_tag("strong")
                strong.string = "BAD:"
                p.append(strong)
                snippet.append(p)
            snippet.append(code)

        # Convert the modified soup to string and then to markdown
        return md(str(soup), heading_style="ATX")

    # Handle HTML files by converting them to Markdown
    if filename.endswith(".html"):
        return md(content, heading_style="ATX")

    # Fallback to returning raw content
    return content


def get_local_path(filename: str) -> str:
    """Get the local path for a given style guide filename.

    Flattens the filename by replacing path separators with dashes and ensures
    the file has a .md extension for uniform storage.

    Args:
        filename: The original filename or relative path from the style guide
            repository (e.g., 'pyguide.md' or 'go/guide.md').

    Returns:
        The full local path to the cached markdown file.
    """
    # Flatten the filename by replacing '/' with '-'
    flattened = filename.replace("/", "-")
    # Use the flattened filename and change extension to .md for uniform storage
    base_name = flattened.rsplit(".", 1)[0]
    return os.path.join(get_guides_dir(), f"{base_name}.md")


def get_guide(language: str, remote: bool = False) -> str:
    """Orchestrate fetching and converting the style guide for a given language.

    Args:
        language: The language to fetch the guide for.
        remote: Whether to force fetching from the web instead of local cache.

    Returns:
        The markdown content of the style guide.

    Raises:
        click.UsageError: If the language is not supported.
    """
    # Look up the filename in the mapping
    filename = LANGUAGE_MAP.get(language.lower())
    if not filename:
        error_msg = f"Language '{language}' is not supported."
        logger.warning(error_msg)
        raise click.UsageError(
            f"{error_msg} Supported languages: {', '.join(sorted(LANGUAGE_MAP.keys()))}"
        )

    local_path = get_local_path(filename)

    # If remote is False, check for local file first
    if not remote and os.path.exists(local_path):
        logger.info("Reading style guide from local file: %s", local_path)
        with open(local_path, "r", encoding="utf-8") as f:
            return f.read()

    # Build the full URL and fetch the raw content
    url = f"{BASE_URL}{filename}"
    content = get_guide_content(url)

    # Convert the content to Markdown format
    markdown_content = convert_to_markdown(content, filename)

    # Save to local cache for future use
    if not os.path.exists(get_guides_dir()):
        os.makedirs(get_guides_dir(), exist_ok=True)

    with open(local_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    logger.debug("Cached style guide locally: %s", local_path)

    return markdown_content


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def cli(ctx: click.Context, verbose: bool) -> None:
    """Pulls the latest Google style guide in markdown format."""
    if verbose:
        logger.setLevel(logging.DEBUG)


@cli.command()
@click.argument("language")
@click.option(
    "--output", "-o", type=click.Path(), help="Path to save the style guide markdown."
)
@click.option("--remote", "-r", is_flag=True, help="Force fetching from the web.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def guide(language: str, output: Optional[str], remote: bool, verbose: bool) -> None:
    """Fetch the style guide for a specific LANGUAGE."""
    if verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Processing style guide for: %s", language)

    try:
        # Fetch and process the style guide
        markdown_content = get_guide(language, remote=remote)

        # Handle output: either save to file or print to stdout
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(markdown_content)
            logger.info("Style guide saved to %s", output)
        else:
            click.echo(markdown_content)

    except (click.ClickException, click.UsageError) as e:
        logger.error("Execution failed: %s", e)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def sync(verbose: bool) -> None:
    """Synchronize all supported style guides from the web to local storage."""
    if verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Synchronizing all style guides...")

    if not os.path.exists(get_guides_dir()):
        os.makedirs(get_guides_dir(), exist_ok=True)

    # Get unique filenames to avoid redundant downloads
    filenames = set(LANGUAGE_MAP.values())

    success_count = 0
    failure_count = 0

    for filename in sorted(filenames):
        logger.info("Syncing %s...", filename)
        try:
            url = f"{BASE_URL}{filename}"
            content = get_guide_content(url)
            markdown_content = convert_to_markdown(content, filename)
            local_path = get_local_path(filename)

            with open(local_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            logger.info("Successfully synced %s to %s", filename, local_path)
            success_count += 1
        except Exception as e:
            logger.error("Failed to sync %s: %s", filename, e)
            failure_count += 1

    logger.info(
        "Sync complete. Successes: %d, Failures: %d", success_count, failure_count
    )


@cli.command()
def languages() -> None:
    """List all supported languages and their aliases."""
    # Group languages by their target guide
    guides = {}
    for lang, filename in LANGUAGE_MAP.items():
        if filename not in guides:
            guides[filename] = []
        guides[filename].append(lang)

    click.echo("Supported languages and their aliases:")
    for filename in sorted(guides.keys()):
        aliases = sorted(guides[filename])

        # Check if the guide is cached
        local_path = get_local_path(filename)
        cached_label = " [cached]" if os.path.exists(local_path) else ""

        click.echo(f"  - {', '.join(aliases)}{cached_label}")


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option("--fix", is_flag=True, help="Automatically fix issues if possible.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def check(paths: Sequence[str], fix: bool, verbose: bool) -> None:
    """Run relevant formatters and linters for given paths."""
    if verbose:
        logger.setLevel(logging.DEBUG)

    # Resolve project root once for trigger file checking
    project_root = Path.cwd()

    # Process each provided path independently
    for path_str in paths:
        _check_path(Path(path_str), project_root, fix=fix)


def _check_path(path: Path, project_root: Path, fix: bool = False) -> None:
    """Apply relevant tools to a single path.

    Args:
        path: The path (file or directory) to check.
        project_root: The root of the project for trigger file discovery.
        fix: Whether to apply automatic fixes.
    """
    logger.info("Checking path: %s", path)

    # Iterate through all supported tool definitions
    for tool in _get_tool_definitions(path):
        if _should_run_tool(tool, path, project_root):
            _run_tool(tool["name"], tool, fix=fix)


def _should_run_tool(tool: dict[str, Any], path: Path, project_root: Path) -> bool:
    """Determine if a tool should be run on the given path based on triggers and extensions.

    Args:
        tool: The tool configuration dictionary.
        path: The path being checked.
        project_root: The project root directory.

    Returns:
        True if the tool should run, False otherwise.
    """
    # Check if any trigger files (like pyproject.toml) exist in the project root
    has_trigger = any((project_root / t).exists() for t in tool["trigger"])

    # For files, also check if the extension matches one of the supported ones
    if path.is_file():
        return has_trigger and path.suffix in tool["extensions"]

    # For directories, the existence of a trigger file is sufficient
    return has_trigger


def _get_tool_definitions(path: Path) -> list[dict[str, Any]]:
    """Define supported tools and their associated triggers, extensions, and commands.

    Args:
        path: The path being checked.

    Returns:
        A list of tool configuration dictionaries.
    """
    path_str = str(path)

    return [
        {
            "name": "ruff",
            "check": ["ruff", "check", "--force-exclude", path_str],
            "check_format": ["ruff", "format", "--check", "--force-exclude", path_str],
            "fix": ["ruff", "check", "--fix", "--force-exclude", path_str],
            "format": ["ruff", "format", "--force-exclude", path_str],
            "trigger": ["pyproject.toml", "ruff.toml", ".ruff.toml"],
            "extensions": [".py"],
        },
        {
            "name": "biome",
            "check": ["npx", "biome", "lint", "--no-errors-on-unmatched", path_str],
            "check_format": ["npx", "biome", "format", "--no-errors-on-unmatched", path_str],
            "fix": ["npx", "biome", "lint", "--write", "--no-errors-on-unmatched", path_str],
            "format": ["npx", "biome", "format", "--write", "--no-errors-on-unmatched", path_str],
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
            "check_format": [
                "npx",
                "prettier",
                "--check",
                "--no-error-on-unmatched-pattern",
                path_str,
            ],
            "format": [
                "npx",
                "prettier",
                "--write",
                "--no-error-on-unmatched-pattern",
                path_str,
            ],
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
    fix: bool = False,
) -> None:
    """Orchestrate the execution of a specific formatting or linting tool.

    Args:
        tool_name: The name of the tool to run.
        tool_config: The tool configuration dictionary.
        fix: Whether to apply automatic fixes.
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
        logger.debug("Tool %s (%s) not found in PATH, skipping.", tool_name, executable)
        return

    logger.info("Running %s...", tool_name)
    try:
        if fix:
            # 1. Run formatters (if available) - these are expected to modify files
            if "format" in tool_config:
                _execute_tool_command(tool_config["format"])

            # 2. Run fixers (if available) - these apply automatic linting fixes
            if "fix" in tool_config:
                _execute_tool_command(tool_config["fix"])
        else:
            # 1. Run check_format (if available) - check-only
            if "check_format" in tool_config:
                logger.debug("Executing: %s", " ".join(tool_config["check_format"]))
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
            logger.debug("Executing: %s", " ".join(tool_config["check"]))
            result = subprocess.run(
                tool_config["check"], capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                click.echo(
                    f"--- {tool_name} findings ---\n{result.stdout}\n{result.stderr}"
                )

    except subprocess.CalledProcessError as e:
        logger.warning("%s failed with exit code %d", tool_name, e.returncode)
        if e.stdout:
            logger.debug("STDOUT: %s", e.stdout)
        if e.stderr:
            logger.debug("STDERR: %s", e.stderr)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Unexpected error while running %s: %s", tool_name, e)


def _execute_tool_command(cmd: list[str]) -> None:
    """Execute a tool command and raise CalledProcessError if it returns a non-zero exit code.

    Args:
        cmd: The command list to execute.

    Raises:
        subprocess.CalledProcessError: If the command returns a non-zero exit code.
    """
    logger.debug("Executing: %s", " ".join(cmd))
    subprocess.run(cmd, capture_output=True, check=True)
