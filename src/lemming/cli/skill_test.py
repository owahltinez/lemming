"""Integration tests for skill management shared through agentcli."""

import json
import pathlib

import pytest
from click.testing import CliRunner

from lemming.cli import cli


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


def test_shared_manager_installs_the_packaged_skill(home):
    """Lemming registers agentcli with its package and skill name."""
    (home / ".claude").mkdir()

    result = CliRunner().invoke(cli, ["skill", "install"])

    assert result.exit_code == 0, result.output
    source = pathlib.Path(__file__).resolve().parents[3] / "SKILL.md"
    targets = (
        home / ".agents" / "skills" / "lemming" / "SKILL.md",
        home / ".claude" / "skills" / "lemming" / "SKILL.md",
    )
    for target in targets:
        assert target.read_bytes() == source.read_bytes()


def test_shared_manager_exposes_structured_status(home):
    """The registered group includes agentcli's status command."""
    result = CliRunner().invoke(cli, ["skill", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["skill"] == "lemming"
