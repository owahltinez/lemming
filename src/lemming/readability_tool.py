import logging
import os
import pathlib as pl
import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

import click
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# Configure logging with structured format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("readability")


def get_guides_dir() -> pl.Path:
    """
    Get the directory where style guides are cached.
    Defaults to 'guides/' in the same directory as this script,
    but can be overridden by the READABILITY_CACHE environment variable.
    """
    cache_env = os.getenv("READABILITY_CACHE")
    if cache_env:
        return pl.Path(cache_env)

    return pl.Path(__file__).parent / "guides"


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

    # Perform the HTTP GET request with a timeout
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


def get_local_path(filename: str) -> pl.Path:
    """
    Get the local path for a given style guide filename.
    """
    # Use the base filename and change extension to .md for uniform storage
    base_name = os.path.basename(filename).split(".")[0]
    return get_guides_dir() / f"{base_name}.md"


def fetch_guide(language: str, remote: bool = False) -> str:
    """
    Orchestrate fetching and converting the style guide for a given language.
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
    if not remote and local_path.exists():
        logger.info(f"Reading style guide from local file: {local_path}")
        return local_path.read_text(encoding="utf-8")

    # Build the full URL and fetch the raw content
    url = f"{BASE_URL}{filename}"
    content = fetch_guide_content(url)

    # Convert the content to Markdown format
    markdown_content = convert_to_markdown(content, filename)

    # Save to local cache for future use
    guides_dir = get_guides_dir()
    if not guides_dir.exists():
        guides_dir.mkdir(parents=True, exist_ok=True)

    local_path.write_text(markdown_content, encoding="utf-8")
    logger.debug(f"Cached style guide locally: {local_path}")

    return markdown_content


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def cli(ctx: click.Context, verbose: bool) -> None:
    """
    Pulls the latest Google style guide for the selected LANGUAGE in markdown format.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)


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
        logger.setLevel(logging.DEBUG)

    logger.info(f"Processing style guide for: {language}")

    try:
        # Fetch and process the style guide
        markdown_content = fetch_guide(language, remote=remote)

        # Handle output: either save to file or print to stdout
        if output:
            pl.Path(output).write_text(markdown_content, encoding="utf-8")
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
        logger.setLevel(logging.DEBUG)

    logger.info("Synchronizing all style guides...")

    guides_dir = get_guides_dir()
    if not guides_dir.exists():
        guides_dir.mkdir(parents=True, exist_ok=True)

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

            local_path.write_text(markdown_content, encoding="utf-8")

            logger.info(f"Successfully synced {filename} to {local_path}")
            success_count += 1
        except Exception as e:
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


def _run_tool(
    tool_name: str, tool_config: dict[str, Any], logger: logging.Logger
) -> None:
    """
    Run a specific formatting or linting tool.
    """
    # Check if the primary command exists in the environment
    cmd = (
        tool_config.get("format") or tool_config.get("check") or tool_config.get("fix")
    )
    if not cmd:
        return

    executable = str(cmd[0])
    if not shutil.which(executable):
        logger.debug(f"Tool {tool_name} ({executable}) not found in PATH, skipping.")
        return

    logger.info(f"Running {tool_name}...")
    try:
        if "format" in tool_config:
            logger.debug(f"Executing: {' '.join(tool_config['format'])}")
            subprocess.run(tool_config["format"], capture_output=True, check=False)

        if "fix" in tool_config:
            logger.debug(f"Executing: {' '.join(tool_config['fix'])}")
            subprocess.run(tool_config["fix"], capture_output=True, check=False)

        if "check" in tool_config:
            logger.debug(f"Executing: {' '.join(tool_config['check'])}")
            result = subprocess.run(
                tool_config["check"], capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                click.echo(
                    f"--- {tool_name} findings ---\n{result.stdout}\n{result.stderr}"
                )
    except Exception as e:
        logger.warning(f"Failed to run {tool_name}: {e}")


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def check(paths: Sequence[str], verbose: bool) -> None:
    """
    Run relevant formatters and linters for given paths.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    for path_str in paths:
        path = pl.Path(path_str)
        logger.info(f"Checking path: {path}")

        # Tool definitions with trigger files and commands
        tools = [
            {
                "name": "ruff",
                "check": ["ruff", "check", str(path)],
                "fix": ["ruff", "check", "--fix", str(path)],
                "format": ["ruff", "format", str(path)],
                "trigger": ["pyproject.toml", "ruff.toml", ".ruff.toml"],
                "extensions": [".py"],
            },
            {
                "name": "biome",
                "check": ["npx", "biome", "lint", str(path)],
                "fix": ["npx", "biome", "lint", "--apply", str(path)],
                "format": ["npx", "biome", "format", "--write", str(path)],
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
                "format": ["npx", "prettier", "--write", str(path)],
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
                "format": ["go", "fmt", str(path)],
                "trigger": ["go.mod"],
                "extensions": [".go"],
            },
        ]

        project_root = pl.Path.cwd()

        for tool in tools:
            # Check if any of the trigger files exist in the project root
            has_trigger = any((project_root / t).exists() for t in tool["trigger"])

            # Check if the path is a file and matches the tool's extensions
            # If path is a directory, we assume it's relevant if trigger exists
            is_relevant = True
            if path.is_file():
                is_relevant = path.suffix in tool["extensions"]

            if has_trigger and is_relevant:
                _run_tool(tool["name"], tool, logger)


# Main entry point for the CLI
def main():
    cli()


if __name__ == "__main__":
    main()
