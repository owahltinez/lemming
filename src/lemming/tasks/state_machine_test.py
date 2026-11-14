import subprocess
import time

import pytest

from .. import models, paths, persistence, snapshots
from . import state_machine

S = models.TaskStatus


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (S.PENDING, S.COMPLETING),
        (S.FAILING, S.RUNNING),
        (S.COMPLETED, S.CANCELLED),
        (S.SUPERSEDED, S.RUNNING),
    ],
)
def test_set_status_rejects_moves_the_table_does_not_list(start, end):
    task = models.Task(id="t", description="d", status=start)
    with pytest.raises(state_machine.TransitionError):
        state_machine.set_status(task, end)
    assert task.status == start


def test_set_status_counts_an_attempt_and_stops_the_clock(monkeypatch):
    task = models.Task(id="t", description="d", rejection="stale")
    state_machine.set_status(task, S.RUNNING, now=100.0)
    assert (task.attempts, task.rejection, task.started_at) == (1, None, 100.0)

    task.pid = 42
    state_machine.set_status(task, S.COMPLETING, now=130.0)
    state_machine.set_status(task, S.COMPLETED, now=160.0)
    assert task.run_time == 60.0
    assert task.pid is None
    assert task.completed_at == 160.0


def test_update_run_time():
    task = models.Task(
        id="1", description="test", last_started_at=time.time() - 5
    )
    state_machine.update_run_time(task)
    assert task.run_time >= 5.0


def test_write_patch_artifact_keeps_non_utf8_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    pre_tree = snapshots.snapshot_tree(tasks_file, work)
    notes = work / "notes.txt"
    notes.write_bytes(b"caf\xe9\n")
    post_tree = snapshots.snapshot_tree(tasks_file, work)

    state_machine.write_patch_artifact(
        tasks_file, "latin1", "attempt-1.patch", pre_tree, post_tree
    )

    notes.unlink()
    patch_file = (
        paths.get_artifacts_dir(tasks_file, "latin1") / "attempt-1.patch"
    )
    subprocess.run(["git", "apply", str(patch_file)], cwd=work, check=True)
    assert notes.read_bytes() == b"caf\xe9\n"


def test_write_patch_artifact_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    pre_tree = snapshots.snapshot_tree(tasks_file, work)
    for change in ("first", "second", "third"):
        (work / "file.txt").write_text(f"{change}\n", encoding="utf-8")
        post_tree = snapshots.snapshot_tree(tasks_file, work)
        state_machine.write_patch_artifact(
            tasks_file, "t1", "attempt-1.patch", pre_tree, post_tree
        )

    artifacts = paths.get_artifacts_dir(tasks_file, "t1")
    assert "+first" in (artifacts / "attempt-1.patch").read_text()
    assert "+second" in (artifacts / "attempt-1-2.patch").read_text()
    assert "+third" in (artifacts / "attempt-1-3.patch").read_text()


def _workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "lemming_home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"
    (work / "a.py").write_text("v1\n", encoding="utf-8")
    return work, tasks_file, snapshots.snapshot_tree(tasks_file, work)


def _save(tasks_file, **fields):
    task = models.Task(id="t1", description="d", **fields)
    persistence.save_tasks(tasks_file, models.Roadmap(tasks=[task]))


def test_settle_returns_an_uncounted_run_to_pending(tmp_path, monkeypatch):
    work, tasks_file, pre_tree = _workspace(tmp_path, monkeypatch)
    _save(tasks_file, status=S.RUNNING, attempts=2, pre_tree=pre_tree, pid=1)
    (work / "a.py").write_text("half_done\n", encoding="utf-8")

    task = state_machine.settle(tasks_file, "t1", work, counted=False)

    assert task is not None
    assert (task.status, task.attempts, task.pid) == (S.PENDING, 1, None)
    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"
    patch = paths.get_artifacts_dir(tasks_file, "t1") / "attempt-2.patch"
    assert "+half_done" in patch.read_text(encoding="utf-8")


def test_settle_keeps_completed_work_across_an_interrupted_review(
    tmp_path, monkeypatch
):
    work, tasks_file, pre_tree = _workspace(tmp_path, monkeypatch)
    _save(tasks_file, status=S.COMPLETING, attempts=1, pre_tree=pre_tree)
    (work / "a.py").write_text("done\n", encoding="utf-8")

    first = state_machine.settle(tasks_file, "t1", work)
    assert first is not None and first.runner_tree is not None

    # A hook was cut off halfway; only its edits are dropped.
    (work / "a.py").write_text("hook_junk\n", encoding="utf-8")
    again = state_machine.settle(
        tasks_file, "t1", work, released_at=time.time()
    )

    assert again is not None
    assert (again.status, again.pid) == (S.COMPLETING, None)
    assert again.runner_tree == first.runner_tree
    assert (work / "a.py").read_text(encoding="utf-8") == "done\n"
    patch = (
        paths.get_artifacts_dir(tasks_file, "t1") / "interrupted-review.patch"
    )
    assert "+hook_junk" in patch.read_text(encoding="utf-8")


def test_conclude_rejected_completion_rewinds_for_the_retry(
    tmp_path, monkeypatch
):
    work, tasks_file, pre_tree = _workspace(tmp_path, monkeypatch)
    (work / "a.py").write_text("rejected\n", encoding="utf-8")
    _save(
        tasks_file,
        status=S.COMPLETING,
        attempts=1,
        pre_tree=pre_tree,
        runner_tree=snapshots.snapshot_tree(tasks_file, work),
        rejection="tests fail",
    )

    task = state_machine.conclude(tasks_file, "t1", work, accepted=False)

    assert task is not None
    assert (task.status, task.attempts, task.rejection) == (S.PENDING, 1, None)
    assert task.runner_tree is None
    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"
    patch = paths.get_artifacts_dir(tasks_file, "t1") / "attempt-1.patch"
    assert "+rejected" in patch.read_text(encoding="utf-8")
