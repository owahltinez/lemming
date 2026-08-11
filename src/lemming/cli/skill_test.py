"""Tests for installing the packaged Agent Skill."""

import pathlib

import pytest
from click.testing import CliRunner

from lemming.cli import cli
from lemming.cli import skill as skill_cmds


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty home directory standing in for the real one."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(
        pathlib.Path, "home", classmethod(lambda cls: fake_home)
    )
    monkeypatch.chdir(tmp_path)
    return fake_home


def _installed(home):
    """Returns the skill manifest in the cross-tool location."""
    return home / ".agents" / "skills" / "lemming" / "SKILL.md"


def test_the_packaged_skill_is_found_from_a_checkout():
    """Running from source must resolve the skill authored at the root."""
    assert skill_cmds.packaged_skill().is_file()


def test_install_writes_the_cross_tool_location(home):
    """One shared directory covers tools that read it, without guessing."""
    result = CliRunner().invoke(cli, ["skill", "install"])

    assert result.exit_code == 0, result.output
    assert "name: lemming" in _installed(home).read_text()


def test_install_refuses_to_replace_without_force(home):
    """A second install must not silently overwrite the first."""
    CliRunner().invoke(cli, ["skill", "install"])

    result = CliRunner().invoke(cli, ["skill", "install"])

    assert result.exit_code != 0
    assert "--force" in result.output


def test_install_replaces_its_own_skill_with_force(home):
    """Refreshing after an upgrade is the expected way to update."""
    CliRunner().invoke(cli, ["skill", "install"])

    result = CliRunner().invoke(cli, ["skill", "install", "--force"])

    assert result.exit_code == 0, result.output
    assert "name: lemming" in _installed(home).read_text()


def test_install_refuses_to_replace_someone_elses_directory(home):
    """--force licenses replacing this skill, not whatever sits at a typo."""
    target = home / ".agents" / "skills" / "lemming"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("name: something-else\n")

    result = CliRunner().invoke(cli, ["skill", "install", "--force"])

    assert result.exit_code != 0
    assert "refusing" in result.output.lower()


def test_install_targets_a_named_directory(home, tmp_path):
    """A repository-scoped install is just a different destination."""
    destination = tmp_path / "repo" / ".agents" / "skills"

    result = CliRunner().invoke(
        cli, ["skill", "install", "--to", str(destination)]
    )

    assert result.exit_code == 0, result.output
    assert (destination / "lemming" / "SKILL.md").is_file()


def test_install_reports_detected_tools_without_writing_to_them(home):
    """Naming the alternatives beats installing four times unasked."""
    (home / ".claude").mkdir()

    result = CliRunner().invoke(cli, ["skill", "install"])

    assert "Claude Code" in result.output
    assert not (home / ".claude" / "skills" / "lemming").exists()


def test_install_all_covers_every_detected_tool(home):
    """One flag for the sweep, once the caller knows what is there."""
    (home / ".claude").mkdir()

    result = CliRunner().invoke(cli, ["skill", "install", "--all"])

    assert result.exit_code == 0, result.output
    assert (home / ".claude" / "skills" / "lemming" / "SKILL.md").is_file()


def test_a_dry_run_touches_nothing(home):
    """A preview that writes is not a preview."""
    result = CliRunner().invoke(cli, ["skill", "install", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "would install" in result.output
    assert not _installed(home).exists()


def test_a_dry_run_predicts_a_refusal(home):
    """Promising a success the real run would decline is worse than nothing."""
    CliRunner().invoke(cli, ["skill", "install"])

    result = CliRunner().invoke(cli, ["skill", "install", "--dry-run"])

    assert "would REFUSE" in result.output


def test_uninstall_removes_the_skill(home):
    """Installing somewhere must be reversible from the same tool."""
    CliRunner().invoke(cli, ["skill", "install"])

    result = CliRunner().invoke(cli, ["skill", "uninstall"])

    assert result.exit_code == 0, result.output
    assert not _installed(home).exists()


def test_uninstall_refuses_a_directory_it_did_not_write(home):
    """Pointing --to somewhere unexpected must fail, not delete work."""
    target = home / ".agents" / "skills" / "lemming"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("name: something-else\n")

    result = CliRunner().invoke(cli, ["skill", "uninstall"])

    assert result.exit_code != 0
    assert (target / "SKILL.md").is_file()


def test_uninstall_reports_when_there_is_nothing_to_do(home):
    """Silence would look like a removal that did not happen."""
    result = CliRunner().invoke(cli, ["skill", "uninstall"])

    assert result.exit_code == 0
    assert "nothing to remove" in result.output
