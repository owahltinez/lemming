"""Integration tests for skill management and documented shell idioms."""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

import pytest
from click.testing import CliRunner

from lemming.cli import main

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _extract_sh_block(markdown: str, prefix: str) -> str:
    """Returns the fenced ``sh`` block in *markdown* starting with *prefix*."""
    for match in re.finditer(r"```sh\n(.*?)\n```", markdown, re.DOTALL):
        block = match.group(1)
        if block.startswith(prefix):
            return block
    raise AssertionError(f"No sh block starting with {prefix!r}")


def _install_fake_codex(bin_dir: pathlib.Path) -> None:
    """Writes a fake ``codex`` runner that edits its worktree and completes."""
    script = bin_dir / "codex"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys, time\n"
        "if ' complete ' in sys.argv[-1]:\n"
        "    cwd = pathlib.Path.cwd()\n"
        "    out = subprocess.check_output(['lemming', 'status', '--json'])\n"
        "    tasks = json.loads(out)['tasks']\n"
        "    running = [t for t in tasks if t['status'] == 'running']\n"
        "    if running:\n"
        "        tid = running[0]['id']\n"
        "        delay = float(os.environ.get('FAKE_RUNNER_SLEEP', '1.5'))\n"
        "        time.sleep(delay)\n"
        "        (cwd / f'{cwd.name}.txt').write_text(f'done {cwd.name}\\n')\n"
        "        subprocess.run(\n"
        "            ['lemming', 'progress', tid, 'implemented'], check=True\n"
        "        )\n"
        "        subprocess.run(['lemming', 'complete', tid], check=True)\n"
        "print(json.dumps({'type': 'item.completed', 'item': {\n"
        "    'type': 'agent_message', 'text': 'Reviewed.'\n"
        "}}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _hermetic_path(venv_bin: pathlib.Path, bin_dir: pathlib.Path) -> str:
    """Builds a PATH with git/timeout/POSIX tools and no host agent CLIs."""
    dirs = [str(venv_bin), str(bin_dir)]
    for tool in ("git", "timeout", "mktemp", "rmdir", "rm", "sleep"):
        found = shutil.which(tool)
        if found:
            parent = str(pathlib.Path(found).parent)
            if parent not in dirs:
                dirs.append(parent)
    for fallback in ("/usr/bin", "/bin"):
        if fallback not in dirs:
            dirs.append(fallback)
    # Shadow host agent CLIs so default runner lookup uses our fake codex.
    for name in ("agy", "opencode", "claude"):
        if shutil.which(name, path=os.pathsep.join(dirs)):
            (bin_dir / name).symlink_to(bin_dir / "codex")
    return os.pathsep.join(dirs)


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

    result = CliRunner().invoke(main.cli, ["skill", "install"])

    assert result.exit_code == 0, result.output
    source = REPO_ROOT / "SKILL.md"
    targets = (
        home / ".agents" / "skills" / "lemming" / "SKILL.md",
        home / ".claude" / "skills" / "lemming" / "SKILL.md",
    )
    for target in targets:
        assert target.read_bytes() == source.read_bytes()


def test_shared_manager_exposes_structured_status(home):
    """The registered group includes agentcli's status command."""
    result = CliRunner().invoke(main.cli, ["skill", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["skill"] == "lemming"


def test_documented_worktree_fanout_shell_idioms(tmp_path):
    """Runs the SKILL.md and WORKSPACE.md shell blocks under /bin/sh."""
    skill_md = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    workspace_md = (REPO_ROOT / "docs" / "WORKSPACE.md").read_text(
        encoding="utf-8"
    )

    waiter = _extract_sh_block(skill_md, "timeout 60m sh -c '")
    assert waiter == _extract_sh_block(workspace_md, "timeout 60m sh -c '")
    setup = _extract_sh_block(
        workspace_md,
        "repo=$(git rev-parse --show-toplevel) || exit 1\n"
        'test -z "$(git -C "$repo" status --porcelain)" || exit 1\n'
        "base=",
    )
    landing = _extract_sh_block(
        workspace_md, "for slug in auth-events billing-csv; do"
    )

    # Spaces in TMPDIR, LEMMING_HOME, and the repo path exercise quoting.
    space_root = tmp_path / "space dir"
    tmpdir = space_root / "tmp dir"
    lemming_home = space_root / "lemming home"
    bin_dir = space_root / "bin dir"
    repo = space_root / "repo root"
    for directory in (tmpdir, lemming_home, bin_dir, repo):
        directory.mkdir(parents=True)
    _install_fake_codex(bin_dir)

    venv_bin = pathlib.Path(sys.executable).parent
    env = {
        **os.environ,
        "HOME": str(space_root),
        "TMPDIR": str(tmpdir),
        "LEMMING_HOME": str(lemming_home),
        "PATH": _hermetic_path(venv_bin, bin_dir),
    }

    def run_sh(script: str, **extra_env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["/bin/sh", "-c", script],
            cwd=repo,
            env={**env, **extra_env},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    init = run_sh(
        "set -e\n"
        "git init -q -b main\n"
        "git config user.email test@example.com\n"
        "git config user.name Test\n"
        "printf 'goal: base\\ntasks: []\\n' > tasks.yml\n"
        "printf 'obsolete\\n' > legacy.txt\n"
        "git add tasks.yml legacy.txt\n"
        "git commit -qm 'Initial foundation'\n"
    )
    assert init.returncode == 0, init.stderr

    # Already-Idle worktrees return at once; CLI errors exit 1 immediately.
    idle_wt_root = tmpdir / "idle root"
    (idle_wt_root / "already idle").mkdir(parents=True)
    start = time.monotonic()
    idle_res = run_sh(f'wt_root="{idle_wt_root}"\n{waiter}\n')
    assert idle_res.returncode == 0, idle_res.stderr
    assert time.monotonic() - start < 5.0

    ((idle_wt_root / "already idle") / "tasks.yml").write_text(
        ":::corrupt\n", encoding="utf-8"
    )
    err_res = run_sh(f'wt_root="{idle_wt_root}"\n{waiter}\n')
    assert err_res.returncode == 1

    # Run documented setup, waiter, re-arm, and sequential landing end-to-end.
    py = f'"{sys.executable}"'
    fast_waiter = waiter.replace('_ "$wt_root"', '_ "$wt_root" 1')
    fanout_res = run_sh(
        "set -e\n"
        f"{setup}\n"
        f"t0=$({py} -c 'import time; print(time.monotonic())')\n"
        f"{fast_waiter}\n"
        f"t1=$({py} -c 'import time; print(time.monotonic())')\n"
        # Re-arm until both streams are Idle before running sequential landing.
        "while :; do\n"
        "  busy=0\n"
        '  for wt in "$wt_root"/*/; do\n'
        '    out=$(lemming -C "$wt" status --brief)\n'
        '    case "$out" in *"Loop Status: Running"*) busy=1 ;; esac\n'
        "  done\n"
        '  [ "$busy" -eq 0 ] && break\n'
        f"  {fast_waiter}\n"
        "done\n"
        f"{landing}\n"
        f'printf "ELAPSED=%s\\n" "$({py} -c "print($t1 - $t0)")"\n',
        FAKE_RUNNER_SLEEP="1.5",
    )
    assert fanout_res.returncode == 0, fanout_res.stdout + fanout_res.stderr
    elapsed_line = [
        line
        for line in fanout_res.stdout.splitlines()
        if line.startswith("ELAPSED=")
    ][-1]
    first_wait = float(elapsed_line.removeprefix("ELAPSED="))
    # Must settle initial `[ ]` -> `[*]` claiming rather than waking on tick 1.
    assert first_wait >= 1.2

    assert (repo / "auth-events.txt").read_text() == "done auth-events\n"
    assert (repo / "billing-csv.txt").read_text() == "done billing-csv\n"
    assert (repo / "tasks.yml").read_text() == "goal: base\ntasks: []\n"
    assert not (repo / "tasks.lock").exists()
    status = run_sh("git status --porcelain")
    assert status.stdout == ""
    merges = run_sh("git log --merges --oneline")
    assert len(merges.stdout.strip().splitlines()) == 2

    # Pure-deletion range skips `--scope -` instead of failing on empty stdin.
    review_guard = landing[landing.index("# Guard against") :]
    pure_del = run_sh(
        "set -e\n"
        "base=$(git rev-parse HEAD)\n"
        "git rm -q legacy.txt\n"
        "git commit -qm 'Remove legacy'\n"
        "repo=$(pwd)\n"
        f"{review_guard}\n"
    )
    assert pure_del.returncode == 0, pure_del.stdout + pure_del.stderr
