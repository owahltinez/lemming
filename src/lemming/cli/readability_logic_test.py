from pathlib import Path
from unittest import mock
from . import readability_logic


def test_should_run_tool_with_trigger(tmp_path):
    project_root = tmp_path
    # Ruff trigger
    (project_root / "pyproject.toml").touch()

    python_file = project_root / "test.py"
    python_file.touch()

    ruff = next(
        t
        for t in readability_logic._get_tool_definitions(python_file)
        if t["name"] == "ruff"
    )

    # Should run on .py file if trigger exists
    assert readability_logic._should_run_tool(ruff, python_file, project_root) is True

    # Should NOT run on .js file
    js_file = project_root / "test.js"
    js_file.touch()
    assert readability_logic._should_run_tool(ruff, js_file, project_root) is False


def test_should_run_tool_no_trigger(tmp_path):
    project_root = tmp_path
    python_file = project_root / "test.py"
    python_file.touch()

    ruff = next(
        t
        for t in readability_logic._get_tool_definitions(python_file)
        if t["name"] == "ruff"
    )

    # Should NOT run if trigger file doesn't exist
    assert readability_logic._should_run_tool(ruff, python_file, project_root) is False


def test_tool_definitions_contain_suppression_flags():
    path = Path("test.py")
    tools = readability_logic._get_tool_definitions(path)

    ruff = next(t for t in tools if t["name"] == "ruff")
    assert "--force-exclude" in ruff["check"]
    assert "--force-exclude" in ruff["fix"]

    biome = next(t for t in tools if t["name"] == "biome")
    assert "-y" in biome["check"]
    assert "npx" == biome["check"][0]
    assert "--no-errors-on-unmatched" in biome["check"]
    assert "--no-errors-on-unmatched" in biome["fix"]

    prettier = next(t for t in tools if t["name"] == "prettier")
    assert "-y" in prettier["check_format"]
    assert "npx" == prettier["check_format"][0]
    assert "--no-error-on-unmatched-pattern" in prettier["check_format"]
    assert "--no-error-on-unmatched-pattern" in prettier["format"]


def test_subprocess_run_uses_timeout():
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        
        # Test _execute_tool_command
        readability_logic._execute_tool_command(["test", "cmd"])
        mock_run.assert_called_with(
            ["test", "cmd"],
            capture_output=True,
            check=True,
            timeout=readability_logic.DEFAULT_TIMEOUT
        )
        
        # Test _run_tool (one branch)
        mock_run.reset_mock()
        with mock.patch("shutil.which", return_value="/usr/bin/ruff"):
            tool_config = {
                "name": "ruff",
                "check": ["ruff", "check", "."],
                "trigger": ["pyproject.toml"],
                "extensions": [".py"],
            }
            readability_logic._run_tool("ruff", tool_config, fix=False)
            mock_run.assert_any_call(
                ["ruff", "check", "."],
                capture_output=True,
                text=True,
                check=False,
                timeout=readability_logic.DEFAULT_TIMEOUT
            )


def test_convert_to_markdown_html():
    content = "<h1>Title</h1><p>Text</p>"
    result = readability_logic.convert_to_markdown(content, "test.html")
    assert "# Title" in result
    assert "Text" in result


def test_get_local_path():
    path = readability_logic.get_local_path("pyguide.md")
    assert path.endswith("pyguide.md")

    path_nested = readability_logic.get_local_path("go/guide.md")
    assert "go-guide.md" in path_nested


@mock.patch("requests.get")
def test_get_guide_content_success(mock_get):
    mock_response = mock.Mock()
    mock_response.text = "content"
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    content = readability_logic.get_guide_content("http://test.com")
    assert content == "content"
    mock_get.assert_called_once_with("http://test.com", timeout=10)
