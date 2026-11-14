import os
import time
from unittest.mock import patch

import pytest

from .. import models, paths, persistence, snapshots
from . import lifecycle, limits, operations, queries


def test_add_task_captures_parent_project(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    parent_file = tmp_path / "parent_tasks.yml"

    with patch.dict(
        os.environ,
        {
            "LEMMING_PARENT_TASK_ID": "parent123",
            "LEMMING_PARENT_TASKS_FILE": str(parent_file),
        },
    ):
        task = operations.add_task(tasks_file, "child task")
        assert task.parent == "parent123"
        assert task.parent_tasks_file == str(parent_file)

    # Manual override
    task2 = operations.add_task(tasks_file, "another task", parent="override")
    assert task2.parent == "override"
    assert task2.parent_tasks_file is None


def test_add_task_inherits_parent_hooks(tmp_path):
    """A roadmap split must not silently drop a per-task hook choice."""
    tasks_file = tmp_path / "tasks.yml"
    parent = operations.add_task(tasks_file, "parent", hooks=["testing"])

    child = operations.add_task(tasks_file, "child", parent=parent.id)
    explicit = operations.add_task(
        tasks_file, "explicit", parent=parent.id, hooks=[]
    )
    orphan = operations.add_task(tasks_file, "orphan", parent="elsewhere")

    assert child.hooks == ["testing"]
    assert explicit.hooks == []
    assert orphan.hooks is None


def test_update_task_hooks(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "task", hooks=["ux"])

    unchanged = operations.update_task(tasks_file, task.id, description="x")
    assert unchanged.hooks == ["ux"]

    cleared = operations.update_task(tasks_file, task.id, hooks=[])
    assert cleared.hooks == []

    reset = operations.update_task(tasks_file, task.id, project_hooks=True)
    assert reset.hooks is None


def test_add_task(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    # Scaffold tasks file
    persistence.save_tasks(tasks_file, models.Roadmap())

    now = time.time()
    task = operations.add_task(tasks_file, "New task")
    assert task.description == "New task"
    assert task.status == models.TaskStatus.PENDING
    assert task.created_at >= now

    data = persistence.load_tasks(tasks_file)
    assert len(data.tasks) == 1
    assert data.tasks[0].created_at == task.created_at


def test_add_task_enforces_description_size_limit(tmp_path, monkeypatch):
    lemming_home = tmp_path / "lemming-home"
    monkeypatch.setenv("LEMMING_HOME", str(lemming_home))
    tasks_file = tmp_path / "project" / "tasks.yml"

    accepted = operations.add_task(
        tasks_file,
        "a" * limits.MAX_TASK_DESCRIPTION_CHARS,
    )
    assert len(accepted.description) == limits.MAX_TASK_DESCRIPTION_CHARS

    with pytest.raises(ValueError) as excinfo:
        operations.add_task(
            tasks_file,
            "b" * (limits.MAX_TASK_DESCRIPTION_CHARS + 1),
        )

    message = str(excinfo.value)
    assert "2,001 characters (limit 2,000)" in message
    assert "move shared rules to the long-term goal" in message
    # Oversized descriptions are steered to the uncapped, auto-delivered brief.
    assert "lemming brief" in message
    assert len(persistence.load_tasks(tasks_file).tasks) == 1


def test_add_task_does_not_reuse_retained_log_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    retained_log = paths.get_log_file(tasks_file, "deadbeef")
    retained_log.write_text("retained")

    with patch(
        "lemming.tasks.operations.lifecycle.generate_task_id",
        side_effect=["deadbeef", "cafebabe"],
    ):
        task = operations.add_task(tasks_file, "New task")

    assert task.id == "cafebabe"
    assert retained_log.read_text() == "retained"


def test_update_task_description(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Old description")
    task_id = task.id

    # 1. Successful update
    updated = operations.update_task(
        tasks_file, task_id, description="New description"
    )
    assert updated.description == "New description"

    # 2. Cannot edit description of a completed task
    operations.update_task(
        tasks_file, task_id, status=models.TaskStatus.COMPLETED
    )

    with pytest.raises(
        ValueError, match="Cannot edit description of a completed task"
    ):
        operations.update_task(
            tasks_file, task_id, description="Trying to change"
        )


def test_update_task_rejects_oversized_description_without_mutating(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Original description")

    with pytest.raises(ValueError, match=r"2,001 characters \(limit 2,000\)"):
        operations.update_task(
            tasks_file,
            task.id,
            description="x" * (limits.MAX_TASK_DESCRIPTION_CHARS + 1),
        )

    persisted = persistence.load_tasks(tasks_file).tasks[0]
    assert persisted.description == "Original description"


def test_legacy_oversized_content_remains_readable_and_updatable(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    legacy_description = "d" * (limits.MAX_TASK_DESCRIPTION_CHARS + 1)
    legacy_progress = "p" * (limits.MAX_PROGRESS_ENTRY_CHARS + 1)
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="legacy",
                    description=legacy_description,
                    progress=[legacy_progress],
                )
            ]
        ),
    )

    loaded = persistence.load_tasks(tasks_file)
    assert loaded.tasks[0].description == legacy_description
    updated = operations.update_task(tasks_file, "legacy", runner="claude")

    assert updated.runner == "claude"
    persisted = persistence.load_tasks(tasks_file).tasks[0]
    assert persisted.description == legacy_description
    assert persisted.progress == [legacy_progress]


def test_update_task_runner(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Task without runner")
    task_id = task.id

    assert task.runner is None

    updated = operations.update_task(
        tasks_file, task_id, runner="custom-runner"
    )
    assert updated.runner == "custom-runner"


def test_update_task_index(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    operations.add_task(tasks_file, "Task 0")
    operations.add_task(tasks_file, "Task 1")
    t2 = operations.add_task(tasks_file, "Task 2")
    task_id = t2.id

    # data.tasks is [Task 0, Task 1, Task 2]
    # Move Task 2 to index 0
    operations.update_task(tasks_file, task_id, index=0)
    data = persistence.load_tasks(tasks_file)
    assert [t.description for t in data.tasks] == ["Task 2", "Task 0", "Task 1"]

    # Move Task 2 to the end
    operations.update_task(tasks_file, task_id, index=-1)
    data = persistence.load_tasks(tasks_file)
    assert [t.description for t in data.tasks] == ["Task 0", "Task 1", "Task 2"]


def test_index_matches_displayed_queue_order(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(id="p1", description="Pending 1"),
                models.Task(id="done", description="Done", status="completed"),
                models.Task(id="p2", description="Pending 2"),
                models.Task(
                    id="active", description="Active", status="in_progress"
                ),
                models.Task(id="p3", description="Pending 3"),
            ]
        ),
    )

    with patch.object(lifecycle, "generate_task_id", return_value="new"):
        operations.add_task(tasks_file, "New", index=3)

    project = queries.get_project_data(tasks_file)
    queue_ids = [
        task.id
        for task in project.tasks
        if task.status not in operations._DONE_STATUSES
    ]
    assert queue_ids == ["active", "p1", "p2", "new", "p3"]

    operations.update_task(tasks_file, "new", index=2)
    project = queries.get_project_data(tasks_file)
    queue_ids = [
        task.id
        for task in project.tasks
        if task.status not in operations._DONE_STATUSES
    ]
    assert queue_ids == ["active", "p1", "new", "p2", "p3"]


def test_index_rejects_positions_that_cannot_be_displayed(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="active", description="Active", status="in_progress"
                ),
                models.Task(id="pending", description="Pending"),
            ]
        ),
    )

    with pytest.raises(ValueError, match="before 1 in-progress"):
        operations.update_task(tasks_file, "pending", index=0)
    with pytest.raises(ValueError, match="maximum 2"):
        operations.add_task(tasks_file, "Too far", index=3)


def test_update_task_status_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Status lifecycle")
    task_id = task.id

    # 1. PENDING -> COMPLETED
    updated = operations.update_task(
        tasks_file, task_id, status=models.TaskStatus.COMPLETED
    )
    assert updated.status == models.TaskStatus.COMPLETED
    assert updated.completed_at is not None

    # 2. COMPLETED -> PENDING resets attempts, completed_at, and snapshots.
    snapshots_dir = snapshots.get_snapshots_dir(tasks_file)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    (snapshots_dir / "HEAD").write_text("ref: refs/heads/main\n")
    with persistence.lock_tasks(tasks_file):
        data = persistence.load_tasks(tasks_file)
        data.tasks[0].attempts = 5
        data.tasks[0].pre_tree = "aaa"
        data.tasks[0].runner_tree = "bbb"
        data.tasks[0].post_tree = "ccc"
        data.tasks[0].pre_head = "ddd"
        data.tasks[0].diff_summary = "1 file (+1/-0): a.py (+1/-0)"
        data.tasks[0].hook_trees = {"readability": ["bbb", "ccc"]}
        persistence.save_tasks(tasks_file, data)

    updated = operations.update_task(
        tasks_file, task_id, status=models.TaskStatus.PENDING
    )
    assert updated.status == models.TaskStatus.PENDING
    assert updated.completed_at is None
    assert updated.attempts == 0
    assert updated.pre_tree is None
    assert updated.runner_tree is None
    assert updated.post_tree is None
    assert updated.pre_head is None
    assert updated.diff_summary is None
    assert updated.hook_trees is None
    assert not snapshots_dir.exists()


def test_update_task_parent_fields(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Update parent fields")
    task_id = task.id

    # 1. Update with values
    updated = operations.update_task(
        tasks_file,
        task_id,
        parent="parent123",
        parent_tasks_file="parent_tasks.yml",
    )
    assert updated.parent == "parent123"
    assert updated.parent_tasks_file == "parent_tasks.yml"

    # 2. Clear values with empty strings
    cleared = operations.update_task(
        tasks_file,
        task_id,
        parent="",
        parent_tasks_file="",
    )
    assert cleared.parent is None
    assert cleared.parent_tasks_file is None


def test_delete_tasks_retains_runner_log(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    operations.add_task(tasks_file, "Task 1")
    t2 = operations.add_task(tasks_file, "Task 2")
    log_file = paths.get_log_file(tasks_file, t2.id)
    log_file.write_text("runner output")

    count = operations.delete_tasks(tasks_file, task_id=t2.id, force=True)
    assert count == 1
    data = persistence.load_tasks(tasks_file)
    assert len(data.tasks) == 1
    assert data.tasks[0].description == "Task 1"
    assert log_file.read_text() == "runner output"


def test_add_task_refuses_to_replace_a_corrupted_roadmap(tmp_path):
    """A corrupt roadmap must survive a mutation instead of being replaced."""
    tasks_file = tmp_path / "tasks.yml"
    operations.add_task(tasks_file, "Task 1")
    corrupt = "tasks: [unclosed\n"
    tasks_file.write_text(corrupt, encoding="utf-8")

    with pytest.raises(persistence.CorruptedTasksError):
        operations.add_task(tasks_file, "Task 2")

    assert tasks_file.read_text(encoding="utf-8") == corrupt
    backup = tmp_path / "tasks.yml.corrupt"
    assert backup.read_text(encoding="utf-8") == corrupt


def test_delete_started_task_requires_explicit_force(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Started task")
    operations.update_task(
        tasks_file, task.id, status=models.TaskStatus.RUNNING
    )

    with pytest.raises(ValueError, match="Supersede it"):
        operations.delete_tasks(tasks_file, task_id=task.id)

    assert persistence.load_tasks(tasks_file).tasks[0].id == task.id


def test_supersede_task_preserves_history(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Large task")
    operations.update_task(
        tasks_file, task.id, status=models.TaskStatus.RUNNING
    )
    log_file = paths.get_log_file(tasks_file, task.id)
    log_file.write_text("partial work")

    superseded = operations.supersede_task(
        tasks_file, task.id, "split after reaching the time limit"
    )

    assert superseded.status == models.TaskStatus.SUPERSEDED
    assert superseded.superseded_at is not None
    assert superseded.superseded_reason == "split after reaching the time limit"
    assert superseded.pid is None
    assert log_file.read_text() == "partial work"


def test_delete_all_tasks_removes_runner_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    task = operations.add_task(tasks_file, "Task")
    log_file = paths.get_log_file(tasks_file, task.id)
    log_file.write_text("runner output")
    snapshots_dir = snapshots.get_snapshots_dir(tasks_file)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    (snapshots_dir / "HEAD").write_text("ref: refs/heads/main\n")

    operations.delete_tasks(tasks_file, all_tasks=True)

    assert not log_file.exists()
    assert not snapshots_dir.exists()


def test_update_task_rejects_ambiguous_prefix(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(id="abc12345", description="First"),
                models.Task(id="abc67890", description="Second"),
            ]
        ),
    )

    with pytest.raises(models.AmbiguousTaskIdError, match="abc12345, abc67890"):
        operations.update_task(tasks_file, "abc", description="Updated")


def test_delete_tasks_rejects_ambiguous_prefix(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(id="abc12345", description="First"),
                models.Task(id="abc67890", description="Second"),
            ]
        ),
    )

    with pytest.raises(models.AmbiguousTaskIdError, match="abc12345, abc67890"):
        operations.delete_tasks(tasks_file, task_id="abc")

    data = persistence.load_tasks(tasks_file)
    assert [task.id for task in data.tasks] == ["abc12345", "abc67890"]


def test_update_goal(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    operations.update_goal(tasks_file, "new goal")
    data = persistence.load_tasks(tasks_file)
    assert data.goal == "new goal"


def test_rollback_task_rewinds_workspace_and_subsequent_started_tasks(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "a.py").write_text("v0\n", encoding="utf-8")
    t1_pre = snapshots.snapshot_tree(tasks_file, work)
    assert t1_pre is not None

    (work / "a.py").write_text("v1\n", encoding="utf-8")
    (work / "b.py").write_text("b1\n", encoding="utf-8")
    t1_post = snapshots.snapshot_tree(tasks_file, work)
    assert t1_post is not None

    (work / "c.py").write_text("c1\n", encoding="utf-8")
    t2_post = snapshots.snapshot_tree(tasks_file, work)
    assert t2_post is not None

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="t0",
                    description="Earlier",
                    status=models.TaskStatus.COMPLETED,
                    started_at=5.0,
                    attempts=1,
                    progress=["done 0"],
                    pre_tree=t1_pre,
                    post_tree=t1_pre,
                ),
                models.Task(
                    id="t1",
                    description="First",
                    status=models.TaskStatus.COMPLETED,
                    started_at=10.0,
                    attempts=1,
                    progress=["done 1"],
                    pre_tree=t1_pre,
                    post_tree=t1_post,
                    diff_summary="2 files (+2/-1)",
                ),
                models.Task(
                    id="t2",
                    description="Second",
                    status=models.TaskStatus.COMPLETED,
                    started_at=20.0,
                    attempts=2,
                    progress=["done 2"],
                    pre_tree=t1_post,
                    post_tree=t2_post,
                    diff_summary="1 file (+1/-0)",
                ),
                models.Task(
                    id="t3",
                    description="Unstarted",
                    status=models.TaskStatus.PENDING,
                ),
            ]
        ),
    )

    reset = operations.rollback_task(tasks_file, "t1", working_dir=work)
    assert [t.id for t in reset] == ["t1", "t2"]
    assert (work / "a.py").read_text(encoding="utf-8") == "v0\n"
    assert not (work / "b.py").exists()
    assert not (work / "c.py").exists()

    persisted = {t.id: t for t in persistence.load_tasks(tasks_file).tasks}
    assert persisted["t0"].status == models.TaskStatus.COMPLETED
    for tid in ("t1", "t2"):
        assert persisted[tid].status == models.TaskStatus.PENDING
        assert persisted[tid].attempts == 0
        assert persisted[tid].progress == []
        assert persisted[tid].pre_tree is None
        assert persisted[tid].post_tree is None
        assert persisted[tid].diff_summary is None
    assert persisted["t3"].status == models.TaskStatus.PENDING


def test_rollback_task_only_reverts_target_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "a.py").write_text("a0\n", encoding="utf-8")
    (work / "b.py").write_text("b0\n", encoding="utf-8")
    t1_pre = snapshots.snapshot_tree(tasks_file, work)
    assert t1_pre is not None

    (work / "a.py").write_text("a1\n", encoding="utf-8")
    t1_post = snapshots.snapshot_tree(tasks_file, work)
    assert t1_post is not None

    (work / "b.py").write_text("b1\n", encoding="utf-8")
    t2_post = snapshots.snapshot_tree(tasks_file, work)
    assert t2_post is not None

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="t1",
                    description="First",
                    status=models.TaskStatus.COMPLETED,
                    started_at=10.0,
                    attempts=1,
                    progress=["done 1"],
                    pre_tree=t1_pre,
                    post_tree=t1_post,
                ),
                models.Task(
                    id="t2",
                    description="Second",
                    status=models.TaskStatus.COMPLETED,
                    started_at=20.0,
                    attempts=1,
                    progress=["done 2"],
                    pre_tree=t1_post,
                    post_tree=t2_post,
                ),
            ]
        ),
    )

    reset = operations.rollback_task(
        tasks_file, "t1", only=True, working_dir=work
    )
    assert [t.id for t in reset] == ["t1"]
    assert (work / "a.py").read_text(encoding="utf-8") == "a0\n"
    assert (work / "b.py").read_text(encoding="utf-8") == "b1\n"

    persisted = {t.id: t for t in persistence.load_tasks(tasks_file).tasks}
    assert persisted["t1"].status == models.TaskStatus.PENDING
    assert persisted["t1"].attempts == 0
    assert persisted["t1"].progress == []
    assert persisted["t1"].pre_tree is None
    assert persisted["t1"].post_tree is None
    assert persisted["t2"].status == models.TaskStatus.COMPLETED
    assert persisted["t2"].post_tree == t2_post


def test_rollback_task_rejects_running_loop_or_missing_snapshots(tmp_path):
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(id="no_pre", description="No pre"),
                models.Task(
                    id="no_post", description="No post", pre_tree="aaa"
                ),
            ]
        ),
    )

    persistence.acquire_loop_lock(tasks_file)
    try:
        with pytest.raises(ValueError, match="loop is running"):
            operations.rollback_task(tasks_file, "no_post")
    finally:
        persistence.release_loop_lock(tasks_file)

    with pytest.raises(ValueError, match="Cannot combine --only and --hook"):
        operations.rollback_task(
            tasks_file, "no_post", only=True, hook="readability"
        )

    with pytest.raises(ValueError, match="no pre_tree"):
        operations.rollback_task(tasks_file, "no_pre")

    with pytest.raises(ValueError, match="no post_tree"):
        operations.rollback_task(tasks_file, "no_post", only=True)

    with pytest.raises(ValueError, match="no snapshot for hook 'readability'"):
        operations.rollback_task(tasks_file, "no_post", hook="readability")


def test_rollback_task_hook_reverts_only_hook_and_keeps_completed(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "a.py").write_text("v0\n", encoding="utf-8")
    pre_tree = snapshots.snapshot_tree(tasks_file, work)
    (work / "a.py").write_text("v1\n", encoding="utf-8")
    runner_tree = snapshots.snapshot_tree(tasks_file, work)
    (work / "a.py").write_text("v1\n# formatted\n", encoding="utf-8")
    post_tree = snapshots.snapshot_tree(tasks_file, work)
    assert pre_tree and runner_tree and post_tree

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="t1",
                    description="First",
                    status=models.TaskStatus.COMPLETED,
                    attempts=1,
                    progress=["done"],
                    pre_tree=pre_tree,
                    runner_tree=runner_tree,
                    post_tree=post_tree,
                    diff_summary=snapshots.summarize_diff(
                        tasks_file, pre_tree, post_tree
                    ),
                    hook_trees={"readability": [runner_tree, post_tree]},
                )
            ]
        ),
    )

    affected = operations.rollback_task(
        tasks_file, "t1", hook="readability", working_dir=work
    )
    assert [t.id for t in affected] == ["t1"]
    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"

    updated = persistence.load_tasks(tasks_file).tasks[0]
    assert updated.status == models.TaskStatus.COMPLETED
    assert updated.attempts == 1
    assert updated.hook_trees is None
    assert updated.post_tree == runner_tree
    assert updated.diff_summary == "1 file (+1/-1): a.py (+1/-1)"


def test_rollback_task_hook_does_not_leak_subsequent_task_changes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "a.py").write_text("v0\n", encoding="utf-8")
    t1_pre = snapshots.snapshot_tree(tasks_file, work)
    (work / "a.py").write_text("v1\n", encoding="utf-8")
    t1_runner = snapshots.snapshot_tree(tasks_file, work)
    (work / "a.py").write_text("v1\n# formatted\n", encoding="utf-8")
    t1_post = snapshots.snapshot_tree(tasks_file, work)
    (work / "b.py").write_text("task2 work\n", encoding="utf-8")
    t2_post = snapshots.snapshot_tree(tasks_file, work)
    assert t1_pre and t1_runner and t1_post and t2_post

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="t1",
                    description="First",
                    status=models.TaskStatus.COMPLETED,
                    started_at=10.0,
                    attempts=1,
                    progress=["done 1"],
                    pre_tree=t1_pre,
                    runner_tree=t1_runner,
                    post_tree=t1_post,
                    diff_summary=snapshots.summarize_diff(
                        tasks_file, t1_pre, t1_post
                    ),
                    hook_trees={"readability": [t1_runner, t1_post]},
                ),
                models.Task(
                    id="t2",
                    description="Second",
                    status=models.TaskStatus.COMPLETED,
                    started_at=20.0,
                    attempts=1,
                    progress=["done 2"],
                    pre_tree=t1_post,
                    runner_tree=t2_post,
                    post_tree=t2_post,
                    diff_summary=snapshots.summarize_diff(
                        tasks_file, t1_post, t2_post
                    ),
                ),
            ]
        ),
    )

    operations.rollback_task(
        tasks_file, "t1", hook="readability", working_dir=work
    )
    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"
    assert (work / "b.py").read_text(encoding="utf-8") == "task2 work\n"

    updated_t1 = persistence.load_tasks(tasks_file).tasks[0]
    assert updated_t1.post_tree == t1_runner
    assert updated_t1.diff_summary == "1 file (+1/-1): a.py (+1/-1)"
    assert "b.py" not in (updated_t1.diff_summary or "")


def test_rollback_task_places_reset_tasks_before_unstarted_pending_tasks(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "a.py").write_text("v0\n", encoding="utf-8")
    t1_pre = snapshots.snapshot_tree(tasks_file, work)
    (work / "a.py").write_text("v1\n", encoding="utf-8")
    t1_post = snapshots.snapshot_tree(tasks_file, work)
    (work / "a.py").write_text("v2\n", encoding="utf-8")
    t2_post = snapshots.snapshot_tree(tasks_file, work)
    assert t1_pre and t1_post and t2_post

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="t1",
                    description="First",
                    status=models.TaskStatus.COMPLETED,
                    started_at=10.0,
                    completed_at=15.0,
                    attempts=1,
                    progress=["done 1"],
                    pre_tree=t1_pre,
                    post_tree=t1_post,
                ),
                models.Task(
                    id="t2",
                    description="Second",
                    status=models.TaskStatus.COMPLETED,
                    started_at=20.0,
                    completed_at=25.0,
                    attempts=1,
                    progress=["done 2"],
                    pre_tree=t1_post,
                    post_tree=t2_post,
                ),
                models.Task(
                    id="t_cancelled_pending",
                    description="Cancelled before ever starting",
                    status=models.TaskStatus.CANCELLED,
                    completed_at=30.0,
                ),
            ]
        ),
    )

    with patch.object(lifecycle, "generate_task_id", return_value="t3"):
        operations.add_task(tasks_file, "Added later")

    before_ids = [t.id for t in persistence.load_tasks(tasks_file).tasks]
    assert before_ids == ["t3", "t1", "t2", "t_cancelled_pending"]

    log1 = paths.get_log_file(tasks_file, "t1")
    log1.write_text("log 1", encoding="utf-8")

    reset = operations.rollback_task(tasks_file, "t1", working_dir=work)
    assert [t.id for t in reset] == ["t1", "t2"]
    assert not log1.exists()

    after_tasks = persistence.load_tasks(tasks_file).tasks
    assert [t.id for t in after_tasks] == [
        "t1",
        "t2",
        "t3",
        "t_cancelled_pending",
    ]
    assert after_tasks[3].status == models.TaskStatus.CANCELLED
    next_up = queries.get_pending_task(persistence.load_tasks(tasks_file))
    assert next_up is not None and next_up.id == "t1"


def test_rollback_task_refuses_dirty_workspace_since_latest_snapshot(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "a.py").write_text("v0\n", encoding="utf-8")
    t1_pre = snapshots.snapshot_tree(tasks_file, work)
    (work / "a.py").write_text("v1\n", encoding="utf-8")
    t1_post = snapshots.snapshot_tree(tasks_file, work)
    assert t1_pre and t1_post

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="t1",
                    description="First",
                    status=models.TaskStatus.COMPLETED,
                    started_at=10.0,
                    attempts=1,
                    progress=["done"],
                    pre_tree=t1_pre,
                    post_tree=t1_post,
                )
            ]
        ),
    )

    (work / "a.py").write_text("uncommitted user edit\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=(
            "Working directory has uncommitted changes since the last task "
            "snapshot"
        ),
    ):
        operations.rollback_task(tasks_file, "t1", working_dir=work)

    assert (work / "a.py").read_text(
        encoding="utf-8"
    ) == "uncommitted user edit\n"
    persisted = persistence.load_tasks(tasks_file).tasks[0]
    assert persisted.status == models.TaskStatus.COMPLETED
    assert persisted.post_tree == t1_post

    # Also refuses dirty workspace when runner_tree/post_tree were cleared.
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="t1",
                    description="First failed",
                    status=models.TaskStatus.FAILED,
                    started_at=10.0,
                    attempts=3,
                    pre_tree=t1_pre,
                )
            ]
        ),
    )
    with pytest.raises(
        ValueError,
        match=(
            "Working directory has uncommitted changes since the last task "
            "snapshot"
        ),
    ):
        operations.rollback_task(tasks_file, "t1", working_dir=work)


def test_rollback_task_multi_hook_updates_chained_hooks_and_post_tree(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "a.py").write_text("v0\n", encoding="utf-8")
    (work / "b.py").write_text("b0\n", encoding="utf-8")
    pre_tree = snapshots.snapshot_tree(tasks_file, work)

    (work / "a.py").write_text("v1\n", encoding="utf-8")
    runner_tree = snapshots.snapshot_tree(tasks_file, work)

    (work / "a.py").write_text("v1\n# formatted\n", encoding="utf-8")
    h1_post = snapshots.snapshot_tree(tasks_file, work)

    (work / "b.py").write_text("b1\n", encoding="utf-8")
    h2_post = snapshots.snapshot_tree(tasks_file, work)
    assert pre_tree and runner_tree and h1_post and h2_post

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="t1",
                    description="Multi-hook task",
                    status=models.TaskStatus.COMPLETED,
                    attempts=1,
                    progress=["done"],
                    pre_tree=pre_tree,
                    runner_tree=runner_tree,
                    post_tree=h2_post,
                    hook_trees={
                        "readability": [runner_tree, h1_post],
                        "testing": [h1_post, h2_post],
                    },
                )
            ]
        ),
    )

    operations.rollback_task(
        tasks_file, "t1", hook="readability", working_dir=work
    )

    assert (work / "a.py").read_text(encoding="utf-8") == "v1\n"
    assert (work / "b.py").read_text(encoding="utf-8") == "b1\n"
    after_h1 = persistence.load_tasks(tasks_file).tasks[0]
    assert after_h1.post_tree is not None and after_h1.post_tree != h2_post
    assert after_h1.hook_trees == {"testing": [runner_tree, after_h1.post_tree]}
    testing_diff = snapshots.diff_trees(
        tasks_file, runner_tree, after_h1.post_tree
    )
    assert "b.py" in testing_diff
    assert "a.py" not in testing_diff

    operations.rollback_task(tasks_file, "t1", hook="testing", working_dir=work)
    assert (work / "b.py").read_text(encoding="utf-8") == "b0\n"
    after_h2 = persistence.load_tasks(tasks_file).tasks[0]
    assert after_h2.post_tree == runner_tree
    assert after_h2.hook_trees is None


def test_rollback_task_only_repositions_target_to_pending_queue(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    tasks_file = work / "tasks.yml"

    (work / "a.py").write_text("a0\n", encoding="utf-8")
    (work / "b.py").write_text("b0\n", encoding="utf-8")
    t1_pre = snapshots.snapshot_tree(tasks_file, work)
    (work / "a.py").write_text("a1\n", encoding="utf-8")
    t1_post = snapshots.snapshot_tree(tasks_file, work)
    (work / "b.py").write_text("b1\n", encoding="utf-8")
    t2_post = snapshots.snapshot_tree(tasks_file, work)
    assert t1_pre and t1_post and t2_post

    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="t0_done",
                    description="Earlier completed",
                    status=models.TaskStatus.COMPLETED,
                ),
                models.Task(
                    id="t1",
                    description="Target completed",
                    status=models.TaskStatus.COMPLETED,
                    started_at=10.0,
                    attempts=1,
                    pre_tree=t1_pre,
                    post_tree=t1_post,
                ),
                models.Task(
                    id="t2_done",
                    description="Later completed",
                    status=models.TaskStatus.COMPLETED,
                    started_at=20.0,
                    attempts=1,
                    pre_tree=t1_post,
                    post_tree=t2_post,
                ),
            ]
        ),
    )

    operations.rollback_task(tasks_file, "t1", only=True, working_dir=work)
    after_ids = [t.id for t in persistence.load_tasks(tasks_file).tasks]
    assert after_ids == ["t1", "t0_done", "t2_done"]


def test_delete_and_update_skip_prune_snapshots_when_loop_active(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMMING_HOME", str(tmp_path / "home"))
    tasks_file = tmp_path / "tasks.yml"
    persistence.save_tasks(
        tasks_file,
        models.Roadmap(
            tasks=[
                models.Task(
                    id="t1",
                    description="Done 1",
                    status=models.TaskStatus.COMPLETED,
                ),
                models.Task(
                    id="t2",
                    description="Done 2",
                    status=models.TaskStatus.COMPLETED,
                ),
            ]
        ),
    )

    persistence.acquire_loop_lock(tasks_file)
    try:
        with patch(
            "lemming.tasks.operations.snapshots.prune_snapshots"
        ) as mock_prune:
            operations.update_task(
                tasks_file, "t1", status=models.TaskStatus.PENDING
            )
            operations.delete_tasks(tasks_file, task_id="t2", force=True)
            mock_prune.assert_not_called()
    finally:
        persistence.release_loop_lock(tasks_file)
