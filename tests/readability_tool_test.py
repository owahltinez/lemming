import os
import subprocess
import sys
from unittest.mock import MagicMock, patch


from lemming.readability_tool import (
    _run_tool,
    check,
    convert_to_markdown,
    get_local_path,
)


def test_convert_to_markdown():
    # Test Markdown content
    assert convert_to_markdown("# Title\nContent", "guide.md") == "# Title\nContent"

    # Test HTML content
    html_content = "<h1>Title</h1><p>Content</p>"
    markdown_output = convert_to_markdown(html_content, "guide.html")
    assert "# Title" in markdown_output
    assert "Content" in markdown_output

    # Test XML content (Vim guide)
    xml_content = "<root>Vim Content</root>"
    assert convert_to_markdown(xml_content, "guide.xml") == "Vim Content"

    # Test fallback
    assert convert_to_markdown("Raw content", "guide.txt") == "Raw content"


def test_get_local_path():
    # Test path construction
    path = get_local_path("python/pyguide.md")
    assert path.name == "pyguide.md"
    assert path.parent.name == "guides"

    path_html = get_local_path("cpp/cppguide.html")
    assert path_html.name == "cppguide.md"


@patch("shutil.which")
@patch("subprocess.run")
def test_run_tool_success(mock_run, mock_which):
    mock_which.return_value = "/usr/bin/tool"
    mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
    logger = MagicMock()

    tool_config = {
        "format": ["tool", "format"],
        "check": ["tool", "check"],
        "fix": ["tool", "fix"],
    }

    _run_tool("mytool", tool_config, logger)

    assert mock_run.call_count == 3
    mock_run.assert_any_call(["tool", "format"], capture_output=True, check=True)
    mock_run.assert_any_call(["tool", "fix"], capture_output=True, check=True)
    mock_run.assert_any_call(
        ["tool", "check"], capture_output=True, text=True, check=False
    )


@patch("shutil.which")
@patch("subprocess.run")
def test_run_tool_missing_executable(mock_run, mock_which):
    mock_which.return_value = None
    logger = MagicMock()

    tool_config = {"check": ["missing", "check"]}

    _run_tool("mytool", tool_config, logger)

    mock_run.assert_not_called()
    logger.debug.assert_called_with(
        "Tool mytool (missing) not found in PATH, skipping."
    )


@patch("shutil.which")
@patch("subprocess.run")
@patch("pathlib.Path.cwd")
def test_check_multiple_paths(mock_cwd, mock_run, mock_which, tmp_path):
    mock_which.return_value = "/usr/bin/ruff"
    mock_run.return_value = MagicMock(returncode=0)
    mock_cwd.return_value = tmp_path

    # Create trigger file
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")

    # Create dummy files
    f1 = tmp_path / "f1.py"
    f2 = tmp_path / "f2.py"
    f1.write_text("", encoding="utf-8")
    f2.write_text("", encoding="utf-8")

    # Call check with multiple paths
    # Note: check is a click command, so we call it via its underlying function or runner
    from click.testing import CliRunner

    runner = CliRunner()
    with patch("lemming.readability_tool._run_tool") as mock_run_tool:
        result = runner.invoke(check, [str(f1), str(f2)])

    assert result.exit_code == 0
    assert mock_run_tool.call_count == 2  # Once for each path


def test_readability_check_ruff(tmp_path):
    # Create a dummy python file with some issues
    py_file = tmp_path / "test.py"
    py_file.write_text("import os\ndef foo():\n  pass\n", encoding="utf-8")

    # Create a pyproject.toml to trigger ruff
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.ruff]\n", encoding="utf-8")

    # Run readability check
    # We need to run it in the tmp_path so it finds the pyproject.toml
    # We use PYTHONPATH to make sure it finds lemming
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{os.getcwd()}/src:{env.get('PYTHONPATH', '')}"

    result = subprocess.run(
        [sys.executable, "-m", "lemming.readability_tool", "check", str(py_file)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    # It should not fail with "No such command"
    assert "No such command 'check'" not in result.stderr


def test_readability_check_no_trigger(tmp_path):
    # Create a dummy python file
    py_file = tmp_path / "test.py"
    py_file.write_text("print('hello')\n", encoding="utf-8")

    # Run readability check WITHOUT trigger file
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{os.getcwd()}/src:{env.get('PYTHONPATH', '')}"

    result = subprocess.run(
        [sys.executable, "-m", "lemming.readability_tool", "check", str(py_file)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    # It should not run any tools since no trigger files exist
    assert "Running ruff" not in result.stderr


def test_readability_check_missing_tool(tmp_path):
    # Create a dummy file
    f = tmp_path / "test.go"
    f.write_text("package main\n", encoding="utf-8")

    # Create a go.mod to trigger go fmt
    gomod = tmp_path / "go.mod"
    gomod.write_text("module test\n", encoding="utf-8")

    # Run readability check
    # If 'go' is not in PATH, it should just skip it and not crash
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{os.getcwd()}/src:{env.get('PYTHONPATH', '')}"
    # This is a bit hard to do reliably, but we can at least check it doesn't crash

    result = subprocess.run(
        [sys.executable, "-m", "lemming.readability_tool", "check", str(f)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    # If go is missing, it should NOT say "Running go fmt"
    # If go is present, it might say it.
    # The important thing is it doesn't crash.


@patch("lemming.readability_tool.fetch_guide_content")
@patch("lemming.readability_tool.get_guides_dir")
def test_fetch_guide_remote(mock_guides_dir, mock_fetch_content, tmp_path):
    mock_guides_dir.return_value = tmp_path
    mock_fetch_content.return_value = "<html><h1>Guide</h1></html>"

    from lemming.readability_tool import fetch_guide

    # Use 'cpp' which uses 'cppguide.html' and triggers conversion
    content = fetch_guide("cpp", remote=True)

    assert "# Guide" in content
    # Should be cached as .md
    assert (tmp_path / "cppguide.md").exists()
    assert (tmp_path / "cppguide.md").read_text(encoding="utf-8") == content


@patch("lemming.readability_tool.get_guides_dir")
def test_fetch_guide_local(mock_guides_dir, tmp_path):
    mock_guides_dir.return_value = tmp_path
    (tmp_path / "pyguide.md").write_text("# Local Content", encoding="utf-8")

    from lemming.readability_tool import fetch_guide

    # Should read from local without calling fetch_guide_content
    with patch("lemming.readability_tool.fetch_guide_content") as mock_fetch:
        content = fetch_guide("python", remote=False)
        mock_fetch.assert_not_called()

    assert content == "# Local Content"


def test_languages_command():
    from click.testing import CliRunner
    from lemming.readability_tool import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["languages"])

    assert result.exit_code == 0
    assert "Supported languages" in result.output
    assert "python" in result.output
    assert "javascript" in result.output


@patch("lemming.readability_tool.fetch_guide_content")
@patch("lemming.readability_tool.get_guides_dir")
def test_sync_command(mock_guides_dir, mock_fetch_content, tmp_path):
    mock_guides_dir.return_value = tmp_path
    mock_fetch_content.return_value = "# Synced Content"

    from click.testing import CliRunner
    from lemming.readability_tool import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["sync"])

    assert result.exit_code == 0
    # Should have created several files
    assert (tmp_path / "pyguide.md").exists()
    assert (tmp_path / "jsguide.md").exists()
    assert (tmp_path / "cppguide.md").exists()


def test_readability_check_extension_filtering(tmp_path):
    # Create a python file
    py_file = tmp_path / "test.py"
    py_file.write_text("print('hello')\n", encoding="utf-8")

    # Create BOTH pyproject.toml and biome.json
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "biome.json").write_text("{}", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{os.getcwd()}/src:{env.get('PYTHONPATH', '')}"

    # Run check with verbose to see what's being run
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lemming.readability_tool",
            "check",
            "--verbose",
            str(py_file),
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    # Ruff should run (trigger + .py)
    assert "Running ruff" in result.stderr
    # Biome should NOT run (trigger present but .py not in extensions)
    assert "Running biome" not in result.stderr


def test_readability_check_directory(tmp_path):
    # Create a directory with a python file
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    py_file = src_dir / "test.py"
    py_file.write_text("print('hello')\n", encoding="utf-8")

    # Create pyproject.toml in the ROOT
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{os.getcwd()}/src:{env.get('PYTHONPATH', '')}"

    # Run check on the directory
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lemming.readability_tool",
            "check",
            "--verbose",
            str(src_dir),
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    # Ruff should run on the directory
    assert "Running ruff" in result.stderr
    assert f"Checking path: {src_dir}" in result.stderr


def test_run_tool_command_failure(tmp_path):
    # Test that if a formatter fails, it's caught and logged
    from lemming.readability_tool import _run_tool
    import subprocess

    logger = MagicMock()
    tool_config = {
        "format": ["false"],  # 'false' command returns 1
        "check": ["echo", "check"],
    }

    with patch("shutil.which", return_value="/usr/bin/false"):
        # We need to mock _execute_tool_command because it uses subprocess.run(check=True)
        # and we want to see if _run_tool catches the CalledProcessError
        with patch("subprocess.run") as mock_run:
            # Simulate CalledProcessError for the first call (format)
            mock_run.side_effect = [
                subprocess.CalledProcessError(1, ["false"], b"", b"error")
            ]

            _run_tool("failing_tool", tool_config, logger)

            # Logger should record the failure
            logger.warning.assert_called_with("failing_tool failed with exit code 1")

            # Subsequent commands (check) should NOT be run if an error occurred in format
            assert mock_run.call_count == 1
