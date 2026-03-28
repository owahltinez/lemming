import logging
import os
import pathlib as pl
import shutil
import subprocess
import sys

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


def get_guides_dir():
    """
    Get the directory where style guides are cached.
    Defaults to 'guides/' in the same directory as this script,
    but can be overridden by the READABILITY_CACHE environment variable.
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


def fetch_guide_content(url):
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


def convert_to_markdown(content, filename):
    """
    Convert the raw content to markdown based on file extension.
    """
    logger.debug(f"Converting content for {filename}")

    # Handle Markdown files directly
    if filename.endswith(".md"):
        return content

    # Handle HTML files by converting them to Markdown
    if filename.endswith(".html"):
        return md(content, heading_style="ATX")

    # Handle XML files (used for Vim script guide)
    if filename.endswith(".xml"):
        soup = BeautifulSoup(content, "html.parser")
        return soup.get_text()

    # Fallback to returning raw content
    return content


def get_local_path(filename):
    """
    Get the local path for a given style guide filename.
    """
    # Use the base filename and change extension to .md for uniform storage
    base_name = os.path.basename(filename).split(".")[0]
    return os.path.join(get_guides_dir(), f"{base_name}.md")


def fetch_guide(language, remote=False):
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
    if not remote and os.path.exists(local_path):
        logger.info(f"Reading style guide from local file: {local_path}")
        with open(local_path, "r") as f:
            return f.read()

    # Build the full URL and fetch the raw content
    url = f"{BASE_URL}{filename}"
    content = fetch_guide_content(url)

    # Convert the content to Markdown format
    markdown_content = convert_to_markdown(content, filename)

    # Save to local cache for future use
    if not os.path.exists(get_guides_dir()):
        os.makedirs(get_guides_dir(), exist_ok=True)

    with open(local_path, "w") as f:
        f.write(markdown_content)
    logger.debug(f"Cached style guide locally: {local_path}")

    return markdown_content


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def cli(ctx, verbose):
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
def fetch(language, output, remote, verbose):
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
            with open(output, "w") as f:
                f.write(markdown_content)
            logger.info(f"Style guide saved to {output}")
        else:
            click.echo(markdown_content)

    except (click.ClickException, click.UsageError) as e:
        logger.error(f"Execution failed: {e}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def sync(verbose):
    """
    Synchronize all supported style guides from the web to local storage.
    """
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
        logger.info(f"Syncing {filename}...")
        try:
            url = f"{BASE_URL}{filename}"
            content = fetch_guide_content(url)
            markdown_content = convert_to_markdown(content, filename)
            local_path = get_local_path(filename)

            with open(local_path, "w") as f:
                f.write(markdown_content)

            logger.info(f"Successfully synced {filename} to {local_path}")
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to sync {filename}: {e}")
            failure_count += 1

    logger.info(f"Sync complete. Successes: {success_count}, Failures: {failure_count}")


@cli.command()
def languages():
    """
    List all supported languages and their aliases.
    """
    # Group languages by their target guide
    guides = {}
    for lang, filename in LANGUAGE_MAP.items():
        if filename not in guides:
            guides[filename] = []
        guides[filename].append(lang)

    click.echo("Supported languages and their aliases:")
    for filename in sorted(guides.keys()):
        aliases = sorted(guides[filename])
        click.echo(f"  - {', '.join(aliases)}")


def _run_tool(tool_name, tool_config, logger):
    """
    Run a specific formatting or linting tool.
    """
    # Check if the primary command exists in the environment
    cmd = (
        tool_config.get("format") or tool_config.get("check") or tool_config.get("fix")
    )
    if not cmd:
        return

    executable = cmd[0]
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
@click.argument("path", type=click.Path(exists=True))
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def check(path, verbose):
    """
    Run relevant formatters and linters for a given path.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    logger.info(f"Checking path: {path}")

    # Tool definitions with trigger files and commands
    tools = [
        {
            "name": "ruff",
            "check": ["ruff", "check", path],
            "fix": ["ruff", "check", "--fix", path],
            "format": ["ruff", "format", path],
            "trigger": ["pyproject.toml", "ruff.toml", ".ruff.toml"],
        },
        {
            "name": "biome",
            "check": ["npx", "biome", "lint", path],
            "fix": ["npx", "biome", "lint", "--apply", path],
            "format": ["npx", "biome", "format", "--write", path],
            "trigger": ["biome.json", "biome.jsonc"],
        },
        {
            "name": "prettier",
            "format": ["npx", "prettier", "--write", path],
            "trigger": [
                ".prettierrc",
                ".prettierrc.json",
                ".prettierrc.yml",
                ".prettierrc.yaml",
                ".prettierrc.js",
                "prettier.config.js",
                "prettier.config.cjs",
            ],
        },
        {
            "name": "go fmt",
            "format": ["go", "fmt", path],
            "trigger": ["go.mod"],
        },
    ]

    project_root = pl.Path.cwd()

    for tool in tools:
        # Check if any of the trigger files exist in the project root
        has_trigger = any((project_root / t).exists() for t in tool["trigger"])

        if has_trigger:
            _run_tool(tool["name"], tool, logger)


# Main entry point for the CLI
def main():
    cli()


if __name__ == "__main__":
    main()
