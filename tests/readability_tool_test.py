import os
import subprocess
import sys


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
