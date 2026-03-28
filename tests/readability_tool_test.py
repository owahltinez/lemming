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
    mock_run.assert_any_call(["tool", "format"], capture_output=True, check=False)
    mock_run.assert_any_call(["tool", "fix"], capture_output=True, check=False)
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
