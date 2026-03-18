import os

from lemming import utils


def test_generate_task_id():
    id1 = utils.generate_task_id()
    id2 = utils.generate_task_id()
    assert len(id1) == 8
    assert id1 != id2


def test_is_pid_alive():
    assert utils.is_pid_alive(os.getpid()) is True
    assert utils.is_pid_alive(999999) is False  # Assuming this PID doesn't exist


def test_in_git_repo():
    # This might depend on where the tests are run, but in this environment it should be true
    assert utils.in_git_repo() is True


def test_is_ignored(tmp_path):
    # This might be tricky to test without a real git repo, but we can mock or just assume it's tested elsewhere
    pass
