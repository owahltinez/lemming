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


def test_readability_languages():
    runner = CliRunner()
    result = runner.invoke(cli, ["readability", "languages"])
    assert result.exit_code == 0
    assert "Supported languages" in result.output


def test_readability_verbose_sync():
    import logging

    runner = CliRunner()
    # This just ensures the command runs with -v,
    # we can't easily check the logger level of a sub-process or if it was modified in-process
    # without more complex mocking, but we can verify it doesn't crash.
    result = runner.invoke(cli, ["-v", "readability", "languages"])
    assert result.exit_code == 0
    assert "Supported languages" in result.output

    # Check that the logger level was actually set in this process
    logger = logging.getLogger("readability")
    assert logger.level == logging.DEBUG


def test_readability_check_ignored_file():
    runner = CliRunner()
    # src/lemming/web/mancha.js is ignored in biome.json
    result = runner.invoke(cli, ["readability", "check", "src/lemming/web/mancha.js"])
    assert result.exit_code == 0
