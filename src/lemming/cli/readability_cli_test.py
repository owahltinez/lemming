import logging
from pathlib import Path

from click.testing import CliRunner

from lemming.cli.main import cli


def test_readability_group_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["readability", "--help"])
    assert result.exit_code == 0
    assert "Run the readability tool for code quality checks" in result.output


def test_readability_check_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["readability", "check", "--help"])
    assert result.exit_code == 0
    assert "check" in result.output.lower()


def test_readability_guide_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["readability", "guide", "--help"])
    assert result.exit_code == 0
    assert "guide" in result.output.lower()


def test_readability_guide_lists_languages_without_one():
    # With no language named, `guide` answers with the language list.
    runner = CliRunner()
    result = runner.invoke(cli, ["readability", "guide"])
    assert result.exit_code == 0
    assert "Supported languages" in result.output


def test_readability_verbose_sets_logger_level():
    runner = CliRunner()
    # This just ensures the command runs with -v,
    # we can't easily check the logger level of a sub-process or if it was
    # modified in-process
    # without more complex mocking, but we can verify it doesn't crash.
    result = runner.invoke(cli, ["-v", "readability", "guide"])
    assert result.exit_code == 0
    assert "Supported languages" in result.output

    # Check that the logger level was actually set in this process
    logger = logging.getLogger("readability")
    assert logger.level == logging.DEBUG


def test_readability_check_ignored_file():
    runner = CliRunner()
    # src/lemming/web/mancha.js is ignored in biome.json
    result = runner.invoke(
        cli, ["readability", "check", "src/lemming/web/mancha.js"]
    )
    assert result.exit_code == 0


def test_readability_leaves_unsupported_markdown_unchanged(tmp_path):
    """The wrapper preserves files that readability does not own."""
    paragraph = (
        "This deliberately long paragraph proves that the lemming command "
        "loads the installed readability dependency while leaving unsupported "
        "Markdown content exactly as the caller wrote it.\n"
    )
    runner = CliRunner()

    with runner.isolated_filesystem(temp_dir=tmp_path):
        markdown = Path("README.md")
        markdown.write_text(f"# Integration\n\n{paragraph}")

        original = markdown.read_text()

        result = runner.invoke(
            cli, ["readability", "check", "--fix", str(markdown)]
        )
        assert result.exit_code == 0
        assert "nothing was checked" in result.output
        assert markdown.read_text() == original
