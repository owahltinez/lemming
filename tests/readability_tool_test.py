import subprocess


def test_readability_check_ruff(tmp_path):
    # Create a dummy python file with some issues
    py_file = tmp_path / "test.py"
    py_file.write_text("import os\ndef foo():\n  pass\n", encoding="utf-8")

    # Create a pyproject.toml to trigger ruff
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.ruff]\n", encoding="utf-8")

    # Run readability check
    # We need to run it in the tmp_path so it finds the pyproject.toml
    result = subprocess.run(
        ["readability", "check", str(py_file)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )

    # It should run ruff and might report something (or nothing if ruff is happy)
    # But it should not fail with "No such command"
    assert "No such command 'check'" not in result.stderr
    assert result.returncode == 0


def test_readability_check_no_trigger(tmp_path):
    # Create a dummy python file
    py_file = tmp_path / "test.py"
    py_file.write_text("print('hello')\n", encoding="utf-8")

    # Run readability check WITHOUT trigger file
    result = subprocess.run(
        ["readability", "check", str(py_file)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # It should not run any tools since no trigger files exist
    assert "Running ruff" not in result.stderr
