"""Shadow Git snapshots for capturing, diffing, and restoring task trees."""

import contextlib
import fcntl
import logging
import os
import pathlib
import re
import shutil
import subprocess
import threading
from collections.abc import Iterable, Iterator

from . import models, paths

logger = logging.getLogger(__name__)

SNAPSHOTS_DIR_NAME = "snapshots.git"

_EXCLUDE_PATTERNS: tuple[str, ...] = tuple(
    "/tasks.yml /tasks.lock /tasks.tmp /tasks.yml.corrupt* /.lemming/".split()
)
_DIFF_BASE_ARGS: tuple[str, ...] = tuple(
    (
        "-c core.quotePath=false -c diff.noprefix=false "
        "-c diff.mnemonicPrefix=false -c diff.renames=false "
        "diff --no-color --no-ext-diff"
    ).split()
)
_UNBORN_RE = re.compile(r"error: '([^']+)' does not have a commit checked out")
_lock_state = threading.local()


def _is_sha(val: str | None) -> bool:
    """Returns True if val is a 40-character lowercase hexadecimal SHA."""
    return bool(
        val and len(val) == 40 and all(c in "0123456789abcdef" for c in val)
    )


def get_snapshots_dir(tasks_file: pathlib.Path) -> pathlib.Path:
    """Returns the bare shadow Git repository path for a tasks file."""
    return paths.get_project_dir(tasks_file) / SNAPSHOTS_DIR_NAME


@contextlib.contextmanager
def _repo_lock(repo_dir: pathlib.Path) -> Iterator[None]:
    """Serializes shadow repository index and prune operations."""
    lock_path = repo_dir.parent / "snapshots.lock"
    held: set[pathlib.Path] = getattr(_lock_state, "held", set())
    _lock_state.held = held
    if lock_path in held:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        # The flock excludes live git writers; a leftover lock is from a kill.
        with contextlib.suppress(OSError):
            (repo_dir / "index.lock").unlink(missing_ok=True)
        held.add(lock_path)
        try:
            yield
        finally:
            held.discard(lock_path)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _env(
    repo_dir: pathlib.Path | None = None,
    working_dir: pathlib.Path | None = None,
) -> dict[str, str]:
    """Builds a clean subprocess environment with inherited GIT_* stripped."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if repo_dir is not None:
        env["GIT_DIR"] = str(repo_dir)
        env["GIT_INDEX_FILE"] = str(repo_dir / "index")
    if working_dir is not None:
        env["GIT_WORK_TREE"] = str(working_dir)
    return env


def _run_git(
    args: list[str],
    repo_dir: pathlib.Path | None = None,
    working_dir: pathlib.Path | None = None,
    cwd: pathlib.Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Runs a git subprocess with isolated environment variables."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or working_dir or repo_dir,
            env=_env(repo_dir=repo_dir, working_dir=working_dir),
            input=input_text,
            capture_output=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = (
            "git command timed out"
            if isinstance(exc, subprocess.TimeoutExpired)
            else str(exc)
        )
        return subprocess.CompletedProcess(["git", *args], 1, "", msg)


def _exclude_patterns(
    tasks_file: pathlib.Path, working_dir: pathlib.Path | None = None
) -> list[str]:
    """Returns git exclude patterns for Lemming metadata and state files."""
    patterns = list(_EXCLUDE_PATTERNS)
    work = working_dir.resolve() if working_dir is not None else None
    if tasks_file.name != "tasks.yml":
        resolved = tasks_file.resolve()
        if work is None or resolved.is_relative_to(work):
            rel_p = (
                resolved.relative_to(work)
                if work
                else pathlib.Path(tasks_file.name)
            )
            rel = rel_p.as_posix()
            lock = rel_p.with_suffix(".lock").as_posix()
            tmp = rel_p.with_suffix(".tmp").as_posix()
            patterns += [f"/{rel}", f"/{lock}", f"/{tmp}", f"/{rel}.corrupt*"]
    if work is not None:
        for d in (paths.get_lemming_home(), paths.get_project_dir(tasks_file)):
            res = d.resolve()
            if res != work and res.is_relative_to(work):
                pat = f"/{res.relative_to(work).as_posix()}/"
                if pat not in patterns:
                    patterns.append(pat)
        host_exc = work / ".git" / "info" / "exclude"
        if host_exc.is_file():
            text = host_exc.read_text(encoding="utf-8", errors="replace")
            for raw in text.splitlines():
                line = raw.rstrip("\r\n")
                if line.strip() and not line.lstrip().startswith("#"):
                    if line not in patterns:
                        patterns.append(line)
    return patterns


def _ensure_repo(
    tasks_file: pathlib.Path, working_dir: pathlib.Path | None = None
) -> pathlib.Path | None:
    """Initializes the shadow bare Git repository if it does not exist."""
    if shutil.which("git") is None:
        return None
    repo_dir = get_snapshots_dir(tasks_file)
    try:
        if not (repo_dir / "HEAD").is_file():
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            cmd = ["-c", "init.defaultRefFormat=files", "init", "--bare", "-q"]
            res = _run_git([*cmd, str(repo_dir)])
            if res.returncode != 0:
                logger.warning("Init failed %s: %s", repo_dir, res.stderr)
                return None
            _run_git(["config", "gc.auto", "0"], repo_dir)
        exc_file = repo_dir / "info" / "exclude"
        if working_dir is not None or not exc_file.is_file():
            exc_file.parent.mkdir(parents=True, exist_ok=True)
            text = "\n".join(_exclude_patterns(tasks_file, working_dir)) + "\n"
            if not exc_file.is_file() or exc_file.read_text() != text:
                exc_file.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to prepare shadow repo %s: %s", repo_dir, exc)
        return None
    return repo_dir


def snapshot_tree(
    tasks_file: pathlib.Path, working_dir: pathlib.Path
) -> str | None:
    """Captures the working directory state as a 40-char Git tree SHA."""
    working_dir = working_dir.resolve()
    if not working_dir.is_dir() or shutil.which("git") is None:
        return None
    with _repo_lock(get_snapshots_dir(tasks_file)):
        repo_dir = _ensure_repo(tasks_file, working_dir)
        if repo_dir is None:
            return None
        if (repo_dir / "index").is_file():
            ign_cmd = ["ls-files", "-i", "-c", "--exclude-standard", "-z"]
            ign = _run_git(ign_cmd, repo_dir, working_dir)
            if ign.returncode == 0 and ign.stdout:
                rm_cmd = ["update-index", "--force-remove", "-z", "--stdin"]
                _run_git(rm_cmd, repo_dir, working_dir, input_text=ign.stdout)
        specs: list[str] = []
        add = _run_git(["add", "-A", "--", "."], repo_dir, working_dir)
        for _ in range(8):
            unborn = _UNBORN_RE.findall(add.stderr) if add.returncode else []
            if not unborn:
                break
            specs.extend(f":(exclude){p.rstrip('/')}" for p in unborn)
            add = _run_git(
                ["add", "-A", "--", ".", *specs], repo_dir, working_dir
            )
        if add.returncode != 0:
            logger.warning("git add failed %s: %s", working_dir, add.stderr)
            return None
        tree = _run_git(["write-tree"], repo_dir, working_dir)
        if tree.returncode != 0:
            logger.warning("write-tree failed %s: %s", working_dir, tree.stderr)
            return None
        sha = tree.stdout.strip()
        if not _is_sha(sha):
            return None
        with contextlib.suppress(OSError):
            refs_dir = repo_dir / "refs" / "snapshots"
            refs_dir.mkdir(parents=True, exist_ok=True)
            (refs_dir / sha).write_text(f"{sha}\n", encoding="utf-8")
        return sha


def head_commit(working_dir: pathlib.Path) -> str | None:
    """Returns the host repository's HEAD commit SHA, or None if unavailable."""
    working_dir = working_dir.resolve()
    if not (working_dir / ".git").exists() or shutil.which("git") is None:
        return None
    cmd = ["-C", str(working_dir), "rev-parse", "--verify", "HEAD"]
    res = _run_git(cmd, cwd=working_dir)
    sha = res.stdout.strip() if res.returncode == 0 else ""
    return sha if _is_sha(sha) else None


def _tree_entries(
    repo_dir: pathlib.Path, tree: str
) -> tuple[set[str], set[str]] | None:
    """Returns (regular_files, gitlink_dirs) in tree, or None on error."""
    res = _run_git(["ls-tree", "-r", "-z", tree], repo_dir)
    if res.returncode != 0:
        return None
    files: set[str] = set()
    gitlinks: set[str] = set()
    for entry in res.stdout.split("\0"):
        if "\t" not in entry:
            continue
        meta, path = entry.split("\t", 1)
        if meta.startswith("160000 "):
            gitlinks.add(path)
        elif path:
            files.add(path)
    return files, gitlinks


def _tree_files(repo_dir: pathlib.Path, tree: str) -> set[str] | None:
    """Lists all non-gitlink file paths recorded in a tree object."""
    entries = _tree_entries(repo_dir, tree)
    return entries[0] if entries is not None else None


def _prune_empty_parents(path: pathlib.Path, stop_dir: pathlib.Path) -> None:
    """Removes empty parent directories of path up to stop_dir."""
    parent = path.parent
    while parent != stop_dir and stop_dir in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def restore_tree(
    tasks_file: pathlib.Path,
    working_dir: pathlib.Path,
    target_tree: str,
    current_tree: str | None = None,
    target_head: str | None = None,
) -> None:
    """Restores the working directory (and optional host HEAD) to a snapshot."""
    working_dir = working_dir.resolve()
    if not _is_sha(target_tree) or not working_dir.is_dir():
        return
    with _repo_lock(get_snapshots_dir(tasks_file)):
        repo_dir = _ensure_repo(tasks_file, working_dir)
        if repo_dir is None:
            return
        if not _is_sha(current_tree):
            current_tree = snapshot_tree(tasks_file, working_dir)
        target_entries = _tree_entries(repo_dir, target_tree)
        read_res = _run_git(["read-tree", target_tree], repo_dir, working_dir)
        if target_entries is None or read_res.returncode != 0:
            logger.warning("read-tree failed: %s", read_res.stderr)
            return
        target_files, target_gitlinks = target_entries
        for rel_path in target_files:
            tp = working_dir / rel_path
            if tp.is_dir() and not tp.is_symlink():
                shutil.rmtree(tp, ignore_errors=True)
            par = tp.parent
            while par != working_dir and working_dir in par.parents:
                if par.is_symlink() or (par.exists() and not par.is_dir()):
                    par.unlink(missing_ok=True)
                par = par.parent
        chk = _run_git(["checkout-index", "-f", "-a"], repo_dir, working_dir)
        if chk.returncode != 0:
            logger.warning("checkout %s failed: %s", target_tree, chk.stderr)
        cur = _tree_entries(repo_dir, current_tree) if current_tree else None
        cur_files, cur_gitlinks = cur if cur else (set(), set())
        extra = (cur_files - target_files) | (cur_gitlinks - target_gitlinks)
        for rel in extra:
            if pathlib.PurePosixPath(rel).name == ".gitignore":
                (working_dir / rel).unlink(missing_ok=True)
        ls_cmd = ["ls-files", "--others", "--exclude-standard", "-z"]
        others = _run_git(ls_cmd, repo_dir, working_dir)
        if others.returncode == 0 and others.stdout:
            extra.update(p.rstrip("/") for p in others.stdout.split("\0") if p)
        for f in target_files | target_gitlinks:
            extra.difference_update(
                str(p) for p in pathlib.PurePosixPath(f).parents
            )
        extra = {
            p
            for p in extra - target_files - target_gitlinks
            if not any(p.startswith(f"{g}/") for g in target_gitlinks)
        }
        if extra:
            ign_in = "\0".join(sorted(extra)) + "\0"
            ign_cmd = ["check-ignore", "--stdin", "-z"]
            ign = _run_git(ign_cmd, repo_dir, working_dir, input_text=ign_in)
            if ign.returncode in (0, 1) and ign.stdout:
                extra -= {p.rstrip("/") for p in ign.stdout.split("\0") if p}
        for rel_path in sorted(extra):
            fp = working_dir / rel_path
            try:
                if fp.is_dir() and not fp.is_symlink():
                    shutil.rmtree(fp)
                else:
                    fp.unlink(missing_ok=True)
                _prune_empty_parents(fp, working_dir)
            except NotADirectoryError:
                pass
            except OSError as exc:
                logger.warning("Failed to remove %s: %s", fp, exc)
    if target_head and _is_sha(target_head) and (working_dir / ".git").exists():
        cmd = ["-C", str(working_dir), "reset", "-q", target_head]
        res = _run_git(cmd, cwd=working_dir)
        if res.returncode != 0:
            logger.warning("reset %s failed: %s", target_head, res.stderr)


def diff_trees(
    tasks_file: pathlib.Path,
    pre_tree: str,
    post_tree: str,
    *,
    stat: bool = False,
    name_only: bool = False,
) -> str:
    """Returns the git diff between two snapshot trees."""
    if not _is_sha(pre_tree) or not _is_sha(post_tree) or pre_tree == post_tree:
        return ""
    repo_dir = get_snapshots_dir(tasks_file)
    if not (repo_dir / "HEAD").is_file():
        return ""
    flag = "--name-only" if name_only else ("--stat" if stat else "--binary")
    res = _run_git([*_DIFF_BASE_ARGS, flag, pre_tree, post_tree], repo_dir)
    return res.stdout if res.returncode == 0 else ""


def changed_files(
    tasks_file: pathlib.Path, pre_tree: str, post_tree: str
) -> list[str]:
    """Returns the list of file paths that changed between two trees."""
    output = diff_trees(tasks_file, pre_tree, post_tree, name_only=True)
    return [line for line in output.splitlines() if line.strip()]


def summarize_diff(
    tasks_file: pathlib.Path, pre_tree: str, post_tree: str
) -> str | None:
    """Builds a compact single-line diff summary, or None if identical."""
    if not _is_sha(pre_tree) or not _is_sha(post_tree) or pre_tree == post_tree:
        return None
    repo_dir = get_snapshots_dir(tasks_file)
    if not (repo_dir / "HEAD").is_file():
        return None
    cmd = [*_DIFF_BASE_ARGS, "--numstat", pre_tree, post_tree]
    res = _run_git(cmd, repo_dir)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    added_sum = deleted_sum = 0
    entries: list[str] = []
    for line in res.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            add = int(parts[0]) if parts[0].isdigit() else 0
            rem = int(parts[1]) if parts[1].isdigit() else 0
            added_sum += add
            deleted_sum += rem
            entries.append(f"{parts[2]} (+{add}/-{rem})")
    if not entries:
        return None
    noun = "file" if len(entries) == 1 else "files"
    return (
        f"{len(entries)} {noun} (+{added_sum}/-{deleted_sum}): "
        f"{', '.join(entries)}"
    )


def revert_tree_patch(
    tasks_file: pathlib.Path,
    working_dir: pathlib.Path,
    pre_tree: str,
    post_tree: str,
) -> None:
    """Reverse-applies a task's tree diff onto the working directory."""
    working_dir = working_dir.resolve()
    if not _is_sha(pre_tree) or not _is_sha(post_tree) or pre_tree == post_tree:
        return
    with _repo_lock(get_snapshots_dir(tasks_file)):
        repo_dir = _ensure_repo(tasks_file, working_dir)
        if repo_dir is None:
            raise ValueError("Shadow snapshots repository is not available")
        before_tree = snapshot_tree(tasks_file, working_dir)
        patch = diff_trees(tasks_file, post_tree, pre_tree)
        if not patch.strip():
            return
        cmd = ["apply", "--3way"]
        res = _run_git(cmd, repo_dir, working_dir, input_text=patch)
        if res.returncode == 0:
            return
        if before_tree is not None:
            restore_tree(tasks_file, working_dir, before_tree)
        raise ValueError(f"Could not revert changes cleanly: {res.stderr}")


def revert_tree_patch_to_tree(
    tasks_file: pathlib.Path,
    base_tree: str,
    patch_pre_tree: str,
    patch_post_tree: str,
) -> str | None:
    """Applies a reverse patch onto base_tree in the shadow index."""
    shas = (base_tree, patch_pre_tree, patch_post_tree)
    if not all(_is_sha(s) for s in shas):
        return None
    if patch_pre_tree == patch_post_tree:
        return base_tree
    with _repo_lock(get_snapshots_dir(tasks_file)):
        repo_dir = _ensure_repo(tasks_file)
        if repo_dir is None:
            return None
        patch = diff_trees(tasks_file, patch_post_tree, patch_pre_tree)
        if not patch.strip():
            return base_tree
        if _run_git(["read-tree", base_tree], repo_dir).returncode != 0:
            return None
        cmd = ["apply", "--cached", "--3way"]
        if _run_git(cmd, repo_dir, input_text=patch).returncode != 0:
            (repo_dir / "index").unlink(missing_ok=True)
            return None
        tree_res = _run_git(["write-tree"], repo_dir)
        sha = tree_res.stdout.strip() if tree_res.returncode == 0 else ""
        return sha if _is_sha(sha) else None


def collect_task_trees(tasks: Iterable[models.Task]) -> set[str]:
    """Collects all referenced snapshot tree SHAs across tasks."""
    candidates: list[str | None] = []
    for task in tasks:
        candidates.extend((task.pre_tree, task.runner_tree, task.post_tree))
        if task.hook_trees:
            for pair in task.hook_trees.values():
                candidates.extend(pair)
    return {sha for sha in candidates if sha is not None and _is_sha(sha)}


def prune_snapshots(
    tasks_file: pathlib.Path, live_trees: Iterable[str]
) -> None:
    """Prunes unreachable snapshot objects, or removes the repo if empty."""
    repo_dir = get_snapshots_dir(tasks_file)
    if not repo_dir.exists():
        return
    live = {sha for sha in live_trees if _is_sha(sha)}
    with _repo_lock(repo_dir):
        if not live:
            shutil.rmtree(repo_dir, ignore_errors=True)
            return
        if not (repo_dir / "HEAD").is_file():
            return
        try:
            refs_dir = repo_dir / "refs" / "snapshots"
            refs_dir.mkdir(parents=True, exist_ok=True)
            for ref_file in refs_dir.iterdir():
                if ref_file.is_file() and ref_file.name not in live:
                    ref_file.unlink(missing_ok=True)
            for sha in live:
                ref_file = refs_dir / sha
                if not ref_file.is_file() or ref_file.read_text() != f"{sha}\n":
                    ref_file.write_text(f"{sha}\n", encoding="utf-8")
            (repo_dir / "index").unlink(missing_ok=True)
            _run_git(["prune", "--expire=now"], repo_dir)
        except OSError as exc:
            logger.warning("Failed to prune snapshots at %s: %s", repo_dir, exc)
