import subprocess
import shutil


def test_readability_command_exists():
    assert shutil.which("readability") is not None


def test_readability_guide_command():
    # Test that 'readability guide' command works (at least doesn't error out on help)
    result = subprocess.run(
        ["readability", "guide", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "guide" in result.stdout.lower()


def test_readability_check_command():
    # Test that 'readability check' command works
    result = subprocess.run(
        ["readability", "check", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "check" in result.stdout.lower()


def test_readability_languages_command():
    # Test that 'readability languages' command works
    result = subprocess.run(
        ["readability", "languages"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "supported languages" in result.stdout.lower()
