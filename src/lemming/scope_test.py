"""Tests for resolving the file scope a review should look at."""

import subprocess

import pytest

from lemming import scope


def _git(working_dir, *args):
    """Runs a git command in the given directory."""
    subprocess.run(
        ["git", *args], cwd=working_dir, check=True, capture_output=True
    )


@pytest.fixture
def repo(tmp_path):
    """A git repository with one committed file on a branch named main."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "committed.py").write_text("x = 1\n")
    _git(tmp_path, "add", "committed.py")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def test_explicit_paths_pass_through_verbatim(repo):
    """A path is already a scope the agent can act on."""
    assert scope.resolve_scope(("src/api/",), repo) == ["src/api/"]


def test_the_whole_repository_needs_no_enumeration(repo):
    """Naming the repo is an instruction, not a file list to compute."""
    assert scope.resolve_scope((".",), repo) == ["."]


def test_several_paths_are_kept_in_order(repo):
    """An explicit list is the caller's own ordering."""
    values = ("src/a.py", "src/b.py")

    assert scope.resolve_scope(values, repo) == ["src/a.py", "src/b.py"]


def test_a_revision_range_resolves_to_changed_files(repo):
    """Only a range needs translating, because git alone can read it."""
    (repo / "feature.py").write_text("y = 2\n")
    _git(repo, "checkout", "-qb", "feature")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-qm", "add feature")

    assert scope.resolve_scope(("main...HEAD",), repo) == ["feature.py"]


def test_the_default_is_uncommitted_work(repo):
    """Committed branch work is an explicit request, not the default."""
    (repo / "committed.py").write_text("x = 2\n")
    (repo / "untracked.py").write_text("z = 3\n")

    assert scope.resolve_scope((), repo) == ["committed.py", "untracked.py"]


def test_the_default_ignores_files_git_ignores(repo):
    """Reviewing a dependency directory would burn the run for nothing."""
    (repo / ".gitignore").write_text("vendor/\n")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "big.py").write_text("noise = True\n")

    assert "vendor/big.py" not in scope.resolve_scope((), repo)


def test_a_clean_tree_resolves_to_nothing(repo):
    """Nothing changed means nothing to review, not everything."""
    assert scope.resolve_scope((), repo) == []


def test_the_default_outside_a_repository_is_the_whole_tree(tmp_path):
    """Without git there is no diff, so the directory is the only scope."""
    assert scope.resolve_scope((), tmp_path) == ["."]


def test_an_oversized_range_yields_the_range_itself(repo):
    """A list too long to read stops being useful context."""
    _git(repo, "checkout", "-qb", "wide")
    for index in range(12):
        (repo / f"file{index}.py").write_text(f"n = {index}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "many files")

    resolved = scope.resolve_scope(("main...HEAD",), repo, max_entries=5)

    assert resolved == ["main...HEAD"]


def test_an_unresolvable_range_is_reported(repo):
    """A typo must fail loudly rather than review the wrong thing."""
    with pytest.raises(scope.ScopeError):
        scope.resolve_scope(("no-such-branch...HEAD",), repo)


def test_a_path_that_does_not_exist_is_still_a_path(repo):
    """Requiring existence would reject globs the agent can expand."""
    assert scope.resolve_scope(("src/**/*.py",), repo) == ["src/**/*.py"]


def test_describe_renders_one_entry_per_line(repo):
    """The prompt block is a list, whatever produced the entries."""
    rendered = scope.describe(["src/a.py", "src/b.py"])

    assert "- src/a.py" in rendered
    assert "- src/b.py" in rendered


def test_describe_reports_an_empty_scope(repo):
    """An agent told to review nothing must be told so explicitly."""
    assert "no changes" in scope.describe([]).lower()
