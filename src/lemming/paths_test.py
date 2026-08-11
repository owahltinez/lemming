import logging
import os
import pathlib
import stat
import subprocess
import time
from unittest import mock

from lemming import paths


def test_get_lemming_home_default():
    expected = pathlib.Path.home() / ".local" / "lemming"
    if "LEMMING_HOME" in os.environ:
        del os.environ["LEMMING_HOME"]
    assert paths.get_lemming_home() == expected


def test_get_lemming_home_override(tmp_path):
    os.environ["LEMMING_HOME"] = str(tmp_path)
    assert paths.get_lemming_home() == tmp_path.resolve()
    del os.environ["LEMMING_HOME"]


def test_get_working_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))

    # A local tasks file: its parent is the working directory
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    tasks_file = project_dir / "tasks.yml"
    tasks_file.touch()
    assert paths.get_working_dir(tasks_file) == project_dir.resolve()

    # An isolated tasks file inside lemming home: fall back to the CWD
    isolated = tmp_path / "home" / "abc123" / "tasks.yml"
    isolated.parent.mkdir(parents=True)
    isolated.touch()
    assert paths.get_working_dir(isolated) == pathlib.Path.cwd().resolve()


def test_get_working_dir_resolves_home_symlink(tmp_path, monkeypatch):
    """A symlinked LEMMING_HOME (e.g. /var -> /private/var on macOS) must
    still recognize tasks files inside the home directory as isolated."""
    real_home = tmp_path / "real_home"
    real_home.mkdir()
    link_home = tmp_path / "link_home"
    link_home.symlink_to(real_home)
    monkeypatch.setenv("LEMMING_HOME", str(link_home))

    isolated = real_home / "abc123" / "tasks.yml"
    isolated.parent.mkdir(parents=True)
    isolated.touch()

    assert paths.get_working_dir(isolated) == pathlib.Path.cwd().resolve()


def test_get_project_dir(tmp_path):
    tasks_file = tmp_path / "my_tasks.yml"
    tasks_file.touch()

    project_dir = paths.get_project_dir(tasks_file)
    assert str(paths.get_lemming_home()) in str(project_dir)

    # Check that it's consistent
    assert paths.get_project_dir(tasks_file) == project_dir


def test_get_default_tasks_file_local(tmp_path):
    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        local_tasks = tmp_path / "tasks.yml"
        local_tasks.touch()
        assert paths.get_default_tasks_file() == tmp_path / "tasks.yml"
    finally:
        os.chdir(orig_cwd)


def test_get_log_file(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    log_file = paths.get_log_file(tasks_file, "task123")
    assert log_file.name == "task123-runner.log"
    assert log_file.parent.exists()


def test_in_git_repo_and_is_ignored(tmp_path):
    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        paths.in_git_repo.cache_clear()

        # Not a git repo yet
        assert paths.in_git_repo() is False
        assert paths.is_ignored(tmp_path / "foo.txt") is False

        # Init git repo
        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], check=True
        )
        subprocess.run(["git", "config", "user.name", "test"], check=True)

        # Clear cache again
        paths.in_git_repo.cache_clear()

        assert paths.in_git_repo() is True

        # Create a gitignore
        (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (tmp_path / "ignored.txt").touch()
        (tmp_path / "not_ignored.txt").touch()

        assert paths.is_ignored(tmp_path / "ignored.txt") is True
        assert paths.is_ignored(tmp_path / "not_ignored.txt") is False

    finally:
        os.chdir(orig_cwd)


class TestParseDotenv:
    def test_basic_key_value(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n")
        assert paths._parse_dotenv(env_file) == {"FOO": "bar", "BAZ": "qux"}

    def test_comments_and_blank_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nFOO=bar\n  # another\n")
        assert paths._parse_dotenv(env_file) == {"FOO": "bar"}

    def test_quoted_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SINGLE='hello world'\nDOUBLE=\"hello world\"\n")
        result = paths._parse_dotenv(env_file)
        assert result == {"SINGLE": "hello world", "DOUBLE": "hello world"}

    def test_export_prefix(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("export FOO=bar\n")
        assert paths._parse_dotenv(env_file) == {"FOO": "bar"}

    def test_value_with_equals(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("URL=https://example.com?a=1&b=2\n")
        assert paths._parse_dotenv(env_file) == {
            "URL": "https://example.com?a=1&b=2"
        }

    def test_missing_file(self, tmp_path):
        assert paths._parse_dotenv(tmp_path / "nope") == {}

    def test_malformed_line_skipped(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("GOOD=yes\nBADLINE\nALSO_GOOD=yes\n")
        result = paths._parse_dotenv(env_file)
        assert result == {"GOOD": "yes", "ALSO_GOOD": "yes"}


class TestLoadDotenv:
    def test_global_env_loaded(self, tmp_path):
        global_env = tmp_path / ".env"
        global_env.write_text("LEMMING_TEST_GLOBAL=from_global\n")
        with mock.patch.object(
            paths, "get_lemming_home", return_value=tmp_path
        ):
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ["PATH"] = "/usr/bin:/bin"
                paths.load_dotenv()
                assert os.environ["LEMMING_TEST_GLOBAL"] == "from_global"

    def test_project_env_overrides_global(self, tmp_path):
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()

        (home_dir / ".env").write_text("MY_VAR=global\nONLY_GLOBAL=yes\n")
        (proj_dir / ".env").write_text("MY_VAR=project\n")

        with mock.patch.object(
            paths, "get_lemming_home", return_value=home_dir
        ):
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ["PATH"] = "/usr/bin:/bin"
                paths.load_dotenv(project_dir=proj_dir)
                assert os.environ["MY_VAR"] == "project"
                assert os.environ["ONLY_GLOBAL"] == "yes"

    def test_real_env_wins(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=from_file\n")
        with mock.patch.object(
            paths, "get_lemming_home", return_value=tmp_path
        ):
            with mock.patch.dict(
                os.environ, {"EXISTING": "from_shell"}, clear=True
            ):
                os.environ["PATH"] = "/usr/bin:/bin"
                paths.load_dotenv()
                assert os.environ["EXISTING"] == "from_shell"

    def test_no_env_files_is_noop(self, tmp_path):
        with mock.patch.object(
            paths, "get_lemming_home", return_value=tmp_path
        ):
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ["PATH"] = "/usr/bin:/bin"
                paths.load_dotenv()  # should not raise


class TestCheckPermissions:
    def test_warns_on_world_readable(self, tmp_path, caplog):
        env_file = tmp_path / ".env"
        env_file.write_text("X=1\n")
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)

        with caplog.at_level(logging.WARNING):
            paths._check_permissions(env_file)
        assert "permissive" in caplog.text


def _age(path: pathlib.Path, days: float) -> None:
    """Backdates a path's modification time by the given number of days."""
    stale = time.time() - days * 86400
    os.utime(path, (stale, stale))


def test_prune_exec_dirs_removes_stale_state(tmp_path, monkeypatch):
    """A failed run keeps its directory, so something has to retire it."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path))
    stale = paths.create_exec_dir()
    (stale / "tasks.yml").write_text("tasks: []")
    _age(stale, paths.EXEC_DIR_RETENTION_DAYS + 1)

    removed = paths.prune_exec_dirs()

    assert removed == 1
    assert not stale.exists()


def test_prune_exec_dirs_keeps_a_recent_failure(tmp_path, monkeypatch):
    """The log of a run that just failed is the reason it was kept."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path))
    recent = paths.create_exec_dir()
    _age(recent, paths.EXEC_DIR_RETENTION_DAYS - 1)

    assert paths.prune_exec_dirs() == 0
    assert recent.exists()


def test_prune_exec_dirs_touches_nothing_else(tmp_path, monkeypatch):
    """Pruning must never reach a project's own state or its hooks."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path))
    project = tmp_path / "abc123def456"
    project.mkdir()
    (project / "tasks.yml").write_text("tasks: []")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "50-custom.md").write_text("custom")
    for path in (project, hooks):
        _age(path, paths.EXEC_DIR_RETENTION_DAYS + 10)

    assert paths.prune_exec_dirs() == 0
    assert (project / "tasks.yml").exists()
    assert (hooks / "50-custom.md").exists()


def test_prune_exec_dirs_survives_an_unremovable_directory(
    tmp_path, monkeypatch
):
    """A directory that cannot be removed must not fail the run."""
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path))
    stale = paths.create_exec_dir()
    _age(stale, paths.EXEC_DIR_RETENTION_DAYS + 1)

    with mock.patch("shutil.rmtree", side_effect=OSError("denied")):
        assert paths.prune_exec_dirs() == 0

    assert stale.exists()


def test_prune_exec_dirs_with_no_home(tmp_path, monkeypatch):
    """A first run has nothing to prune and must not create anything."""
    missing = tmp_path / "not-created-yet"
    monkeypatch.setenv("LEMMING_HOME", str(missing))

    assert paths.prune_exec_dirs() == 0
    assert not missing.exists()
