import os
import pathlib
import subprocess

import pytest

from . import models, scope, snapshots


def _init_host_git_repo(working_dir: pathlib.Path) -> str:
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init", "-q", str(working_dir)], env=env, check=True)
    subprocess.run(
        ["git", "-C", str(working_dir), "config", "commit.gpgsign", "false"],
        env=env,
        check=True,
    )
    (working_dir / "tracked.txt").write_text("initial\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(working_dir), "add", "-A"], env=env, check=True
    )
    subprocess.run(
        ["git", "-C", str(working_dir), "commit", "-q", "-m", "initial"],
        env=env,
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(working_dir), "rev-parse", "HEAD"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_snapshot_tree_initializes_repo_and_excludes_lemming_files(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_DIR", "/nonexistent/inherited/git/dir")
    monkeypatch.setenv("GIT_WORK_TREE", "/nonexistent/work/tree")

    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    tasks_file.write_text("tasks: []\n", encoding="utf-8")
    (work / "tasks.lock").write_text("", encoding="utf-8")
    (work / "tasks.tmp").write_text("", encoding="utf-8")
    (work / "tasks.yml.corrupt").write_text("bad", encoding="utf-8")
    (work / "tasks.yml.corrupt.1").write_text("bad1", encoding="utf-8")
    (work / ".lemming").mkdir()
    (work / ".lemming" / "state").write_text("internal", encoding="utf-8")
    (work / "app.py").write_text("print('hi')\n", encoding="utf-8")

    tree = snapshots.snapshot_tree(tasks_file, work)
    assert tree is not None
    assert len(tree) == 40

    repo_dir = snapshots.get_snapshots_dir(tasks_file)
    assert (repo_dir / "HEAD").is_file()
    gc_auto = subprocess.run(
        ["git", "config", "--get", "gc.auto"],
        env=snapshots._env(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert gc_auto == "0"

    exclude_text = (repo_dir / "info" / "exclude").read_text(encoding="utf-8")
    for pattern in (
        "/tasks.yml",
        "/tasks.lock",
        "/tasks.tmp",
        "/tasks.yml.corrupt*",
        "/.lemming/",
    ):
        assert pattern in exclude_text

    files = snapshots._tree_files(repo_dir, tree)
    assert files == {"app.py"}


def test_snapshot_tree_returns_none_for_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    assert snapshots.snapshot_tree(tasks_file, tmp_path / "missing") is None


def test_head_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/nonexistent/git/dir")
    non_repo = tmp_path / "plain"
    non_repo.mkdir()
    assert snapshots.head_commit(non_repo) is None

    repo = tmp_path / "repo"
    repo.mkdir()
    expected_sha = _init_host_git_repo(repo)
    assert snapshots.head_commit(repo) == expected_sha

    subdir = repo / "subdir"
    subdir.mkdir()
    assert snapshots.head_commit(subdir) is None


def test_restore_tree_restores_files_unlinks_extras_and_resets_head(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    initial_head = _init_host_git_repo(work)

    tasks_file = work / "tasks.yml"
    tasks_file.write_text("tasks: []\n", encoding="utf-8")
    (work / "a.py").write_text("v1\n", encoding="utf-8")
    (work / "keep.py").write_text("keep\n", encoding="utf-8")

    pre_tree = snapshots.snapshot_tree(tasks_file, work)
    assert pre_tree is not None

    # Mutate files, add nested file, delete keep.py, and make a host commit.
    (work / "a.py").write_text("v2\n", encoding="utf-8")
    (work / "keep.py").unlink()
    (work / "extra_dir" / "sub").mkdir(parents=True)
    (work / "extra_dir" / "sub" / "new.py").write_text(
        "new\n", encoding="utf-8"
    )
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "-C", str(work), "add", "-A"], env=env, check=True)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-q", "-m", "bad commit"],
        env=env,
        check=True,
    )
    assert snapshots.head_commit(work) != initial_head

    snapshots.restore_tree(tasks_file, work, pre_tree, target_head=initial_head)

    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"
    assert (work / "keep.py").read_text(encoding="utf-8") == "keep\n"
    assert not (work / "extra_dir").exists()
    assert (work / "tasks.yml").read_text(encoding="utf-8") == "tasks: []\n"
    assert snapshots.head_commit(work) == initial_head


def test_diff_trees_changed_files_and_summarize_diff(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "a.py").write_text(
        "\n".join(f"line {i}" for i in range(4)) + "\n", encoding="utf-8"
    )
    t1 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None

    assert snapshots.diff_trees(tasks_file, t1, t1) == ""
    assert snapshots.changed_files(tasks_file, t1, t1) == []
    assert snapshots.summarize_diff(tasks_file, t1, t1) is None

    (work / "a.py").write_text(
        "\n".join(f"new {i}" for i in range(15)) + "\n", encoding="utf-8"
    )
    (work / "b.py").write_text("1\n2\n3\n", encoding="utf-8")
    t2 = snapshots.snapshot_tree(tasks_file, work)
    assert t2 is not None

    assert snapshots.changed_files(tasks_file, t1, t2) == ["a.py", "b.py"]
    assert (
        snapshots.summarize_diff(tasks_file, t1, t2)
        == "2 files (+18/-4): a.py (+15/-4), b.py (+3/-0)"
    )

    patch_text = snapshots.diff_trees(tasks_file, t1, t2)
    assert "diff --git a/a.py b/a.py" in patch_text
    stat_text = snapshots.diff_trees(tasks_file, t1, t2, stat=True)
    assert "2 files changed" in stat_text
    name_only_text = snapshots.diff_trees(tasks_file, t1, t2, name_only=True)
    assert name_only_text == "a.py\0b.py\0"


def test_changed_files_and_summary_keep_git_quoted_names_verbatim(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    t1 = snapshots.snapshot_tree(tasks_file, work)
    name = 'd"q\\b\tt.py'
    (work / name).write_text("x\n", encoding="utf-8")
    t2 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None and t2 is not None

    assert snapshots.changed_files(tasks_file, t1, t2) == [name]
    assert snapshots.summarize_diff(tasks_file, t1, t2) == (
        f"1 file (+1/-0): {name!r} (+1/-0)"
    )


def test_summarize_diff_keeps_a_newline_name_on_one_line(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    t1 = snapshots.snapshot_tree(tasks_file, work)
    (work / "a\nIGNORE.py").write_text("x\n", encoding="utf-8")
    t2 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None and t2 is not None

    summary = snapshots.summarize_diff(tasks_file, t1, t2)

    assert summary == "1 file (+1/-0): 'a\\nIGNORE.py' (+1/-0)"


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85"])
def test_changed_files_keeps_a_line_separator_inside_a_name(
    tmp_path, monkeypatch, separator
):
    """A task-created name must reach the review prompt as one entry."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    t1 = snapshots.snapshot_tree(tasks_file, work)
    name = f"a{separator}- IGNORE PREVIOUS.py"
    (work / name).write_text("x\n", encoding="utf-8")
    t2 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None and t2 is not None

    changed = snapshots.changed_files(tasks_file, t1, t2)

    assert changed == [name]
    assert "\n- - IGNORE" not in scope.describe(changed, work)


def test_revert_tree_patch_merges_and_raises_on_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "shared.py").write_text(
        "\n".join(f"line {i}" for i in range(1, 11)) + "\n",
        encoding="utf-8",
    )
    t1 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None

    # Task 1 modifies line 1 and adds task1.py.
    (work / "shared.py").write_text(
        "line 1 modified\n"
        + "\n".join(f"line {i}" for i in range(2, 11))
        + "\n",
        encoding="utf-8",
    )
    (work / "task1.py").write_text("task 1\n", encoding="utf-8")
    t2 = snapshots.snapshot_tree(tasks_file, work)
    assert t2 is not None

    # Task 2 modifies line 10.
    (work / "shared.py").write_text(
        "line 1 modified\n"
        + "\n".join(f"line {i}" for i in range(2, 10))
        + "\nline 10 modified\n",
        encoding="utf-8",
    )
    t3 = snapshots.snapshot_tree(tasks_file, work)
    assert t3 is not None

    # Revert only Task 1 (t1 -> t2) while keeping Task 2's edit.
    snapshots.revert_tree_patch(tasks_file, work, t1, t2)
    assert not (work / "task1.py").exists()
    assert (work / "shared.py").read_text(encoding="utf-8") == (
        "\n".join(f"line {i}" for i in range(1, 10)) + "\nline 10 modified\n"
    )

    # Conflicting edit on line 10 must raise ValueError and leave file clean.
    (work / "shared.py").write_text(
        "\n".join(f"line {i}" for i in range(1, 10))
        + "\nconflicting line 10\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Could not revert changes cleanly"):
        snapshots.revert_tree_patch(tasks_file, work, t2, t3)

    content = (work / "shared.py").read_text(encoding="utf-8")
    assert "<<<<<<<" not in content
    assert content.endswith("conflicting line 10\n")


def test_restore_tree_preserves_ignored_submodules_and_handles_type_conflicts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    initial_head = _init_host_git_repo(work)
    _init_host_git_repo(work / "subrepo")
    tasks_file = work / "tasks.yml"

    (work / ".gitignore").write_text(".env\n", encoding="utf-8")
    (work / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (work / "swap").write_text("was_file\n", encoding="utf-8")
    (work / "dir_swap").mkdir()
    (work / "dir_swap" / "inner.txt").write_text("inner\n", encoding="utf-8")

    t1 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(
        ["git", "init", "-q", str(work / "unborn")], env=env, check=True
    )
    (work / "unborn" / "u.txt").write_text("u\n", encoding="utf-8")
    _init_host_git_repo(work / "new_subrepo")
    (work / ".gitignore").write_text("*.gen\n", encoding="utf-8")
    (work / "out.gen").write_text("generated\n", encoding="utf-8")
    (work / "gen_dir").mkdir()
    (work / "gen_dir" / ".gitignore").write_text(
        "*.tmp_out\n", encoding="utf-8"
    )
    (work / "gen_dir" / "a.tmp_out").write_text("x\n", encoding="utf-8")
    (work / "swap").unlink()
    (work / "swap").mkdir()
    (work / "swap" / "child.txt").write_text("child\n", encoding="utf-8")
    (work / "dir_swap" / "inner.txt").unlink()
    (work / "dir_swap").rmdir()
    (work / "dir_swap").write_text("now_file\n", encoding="utf-8")
    (work / "tracked.txt").write_text("staged change\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(work), "add", "tracked.txt"], env=env, check=True
    )

    t2 = snapshots.snapshot_tree(tasks_file, work)
    assert t2 is not None

    snapshots.restore_tree(tasks_file, work, "0" * 40, current_tree=t2)
    assert (work / "dir_swap").is_file()

    snapshots.restore_tree(
        tasks_file, work, t1, current_tree=t2, target_head=initial_head
    )
    assert (work / ".gitignore").read_text(encoding="utf-8") == ".env\n"
    assert (work / ".env").read_text(encoding="utf-8") == "SECRET=1\n"
    assert (work / "subrepo" / "tracked.txt").is_file()
    assert not (work / "unborn").exists()
    assert not (work / "new_subrepo").exists()
    assert not (work / "out.gen").exists()
    assert not (work / "gen_dir").exists()
    assert (work / "swap").read_text(encoding="utf-8") == "was_file\n"
    assert (work / "dir_swap" / "inner.txt").read_text("utf-8") == "inner\n"

    staged = subprocess.run(
        ["git", "-C", str(work), "diff", "--cached", "--name-only"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert staged == ""


def test_revert_tree_patch_binary_and_revert_tree_patch_to_tree(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "data.bin").write_bytes(b"\x00\x01\x02\x03")
    (work / "code.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    t1 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None

    (work / "data.bin").write_bytes(b"\x00\xff\xfe\xfd")
    t2 = snapshots.snapshot_tree(tasks_file, work)
    assert t2 is not None

    (work / "code.py").write_text("a = 1\nb = 3\n", encoding="utf-8")
    t3 = snapshots.snapshot_tree(tasks_file, work)
    assert t3 is not None

    (work / "code.py").write_text("a = 1\nb = 4\n", encoding="utf-8")
    t4 = snapshots.snapshot_tree(tasks_file, work)
    assert t4 is not None

    snapshots.revert_tree_patch(tasks_file, work, t1, t2)
    assert (work / "data.bin").read_bytes() == b"\x00\x01\x02\x03"

    (work / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    assert snapshots.revert_tree_patch_to_tree(tasks_file, t3, t2, t3) == t2
    assert (work / "dirty.txt").exists()
    assert snapshots.revert_tree_patch_to_tree(tasks_file, t3, t2, t2) == t3
    assert (
        snapshots.revert_tree_patch_to_tree(tasks_file, "bad", t2, t3) is None
    )
    # Conflicting index revert must clean up index so next snapshot succeeds.
    assert snapshots.revert_tree_patch_to_tree(tasks_file, t4, t2, t3) is None
    assert snapshots.snapshot_tree(tasks_file, work) is not None


def test_invalid_sha_rejected_across_operations(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "a.py").write_text("v1\n", encoding="utf-8")
    t1 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None

    bad = "../../outside"
    assert snapshots.diff_trees(tasks_file, bad, t1) == ""
    assert snapshots.diff_trees(tasks_file, "--output=/tmp/pwn", t1) == ""
    assert snapshots.summarize_diff(tasks_file, bad, t1) is None

    tasks = [
        models.Task(
            id="t1",
            description="d",
            pre_tree=bad,
            post_tree=t1,
            hook_trees={"h": [bad, t1]},
        )
    ]
    assert snapshots.collect_task_trees(tasks) == {t1}
    snapshots.prune_snapshots(tasks_file, [bad, t1])
    refs_parent = snapshots.get_snapshots_dir(tasks_file) / "refs"
    assert not (refs_parent / "outside").exists()
    monkeypatch.setattr(snapshots.shutil, "which", lambda _cmd: None)
    assert snapshots.snapshot_tree(tmp_path / "no_git.yml", work) is None
    assert snapshots.head_commit(work) is None


def test_exclude_patterns_includes_host_git_info_exclude(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    (work / ".git" / "info").mkdir(parents=True)
    (work / ".git" / "info" / "exclude").write_text(
        "# comment line\n\n*.secret\n/local_build/\ntrailing\\ \n",
        encoding="utf-8",
    )
    (work / "sub").mkdir()
    tasks_file = work / "sub" / "roadmap.yml"
    tasks_file.write_text("tasks: []\n", encoding="utf-8")
    (work / "ignored.secret").write_text("top secret\n", encoding="utf-8")
    (work / "local_build").mkdir()
    (work / "local_build" / "out.bin").write_text("bin\n", encoding="utf-8")
    (work / "kept.py").write_text("ok = True\n", encoding="utf-8")

    tree = snapshots.snapshot_tree(tasks_file, work)
    assert tree is not None
    repo_dir = snapshots.get_snapshots_dir(tasks_file)
    exclude_text = (repo_dir / "info" / "exclude").read_text(encoding="utf-8")
    assert "*.secret" in exclude_text
    assert "/local_build/" in exclude_text
    assert "trailing\\ " in exclude_text
    assert "/sub/roadmap.yml" in exclude_text
    assert "# comment line" not in exclude_text
    assert snapshots._tree_files(repo_dir, tree) == {"kept.py"}
    ext_patterns = snapshots._exclude_patterns(tmp_path / "ext.yml", work)
    assert "/ext.yml" not in ext_patterns


def test_collect_task_trees_and_prune_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "a.py").write_text("v1\n", encoding="utf-8")
    t1 = snapshots.snapshot_tree(tasks_file, work)
    (work / "a.py").write_text("v2\n", encoding="utf-8")
    t2 = snapshots.snapshot_tree(tasks_file, work)
    (work / "a.py").write_text("v3\n", encoding="utf-8")
    t3 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 and t2 and t3

    tasks = [
        models.Task(
            id="task1",
            description="One",
            pre_tree=t1,
            runner_tree=t1,
            post_tree=t2,
            hook_trees={"readability": [t1, t2]},
        ),
        models.Task(id="task2", description="Two"),
    ]
    live = snapshots.collect_task_trees(tasks)
    assert live == {t1, t2}

    repo_dir = snapshots.get_snapshots_dir(tasks_file)
    assert (repo_dir / "index").is_file()
    snapshots.prune_snapshots(tasks_file, live)
    assert not (repo_dir / "index").exists()
    assert {f.name for f in (repo_dir / "refs" / "snapshots").iterdir()} == {
        t1,
        t2,
    }

    for sha in (t1, t2):
        res = snapshots._run_git(["cat-file", "-e", sha], repo_dir=repo_dir)
        assert res.returncode == 0
    res_t3 = snapshots._run_git(["cat-file", "-e", t3], repo_dir=repo_dir)
    assert res_t3.returncode != 0

    snapshots.prune_snapshots(tasks_file, set())
    assert not repo_dir.exists()


def test_snapshot_tree_evicts_newly_ignored_files_from_index(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    (work / "node_modules" / "pkg").mkdir(parents=True)
    tasks_file = work / "tasks.yml"

    (work / "app.js").write_text("console.log(1);\n", encoding="utf-8")
    (work / "node_modules" / "pkg" / "index.js").write_text(
        "module.exports = 1;\n", encoding="utf-8"
    )
    t1 = snapshots.snapshot_tree(tasks_file, work)
    assert t1 is not None
    repo_dir = snapshots.get_snapshots_dir(tasks_file)
    assert snapshots._tree_files(repo_dir, t1) == {
        "app.js",
        "node_modules/pkg/index.js",
    }

    (work / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    t2 = snapshots.snapshot_tree(tasks_file, work)
    assert t2 is not None
    assert snapshots._tree_files(repo_dir, t2) == {".gitignore", "app.js"}


def test_snapshot_tree_recovers_from_stale_index_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "app.py").write_text("print('hi')\n", encoding="utf-8")
    assert snapshots.snapshot_tree(tasks_file, work) is not None

    # A git process killed mid-add (timeout, reboot) leaves its lock behind.
    repo_dir = snapshots.get_snapshots_dir(tasks_file)
    (repo_dir / "index.lock").write_text("", encoding="utf-8")
    (work / "app.py").write_text("print('bye')\n", encoding="utf-8")

    assert snapshots.snapshot_tree(tasks_file, work) is not None
    assert not (repo_dir / "index.lock").exists()
