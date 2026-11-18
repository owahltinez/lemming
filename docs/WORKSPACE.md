# Workspace Isolation 🧰

## Lemming is VCS-Agnostic

An agent run writes to a real directory on your disk. The orchestrator never
creates a branch, a commit, or a worktree in your project's repository: the
task-runner prompt allows the agent itself to commit when that is the project's
convention, but the orchestrator neither asks for that nor relies on it.

When `git` is installed on the host, Lemming maintains an isolated **shadow Git
repository** alongside `tasks.yml` that snapshots the working tree before and
after each task runner and orchestrator hook — without touching your `.git`
index, branches, or commit history. If an attempt fails, times out, crashes, or
is cancelled, Lemming saves its changes to an `attempt-<N>.patch` artifact and
restores the workspace (and host Git `HEAD`, if the runner committed) to the
task's pre-run snapshot. An interrupted loop (a signal or `lemming stop`) does
this on its way out; one killed outright leaves it to the next `lemming run`.
Either way, a task whose agent had already completed keeps that work and only
restarts its reviews. An interrupted `lemming exec` also cancels its task, since
no later run will reclaim it.

Shadow snapshots work in a Git checkout, a Mercurial repository, or a plain
directory under no version control at all, as long as `git` is on `PATH`. Even
so, concurrent writing runs still need separate working copies, and an outer
branch or worktree remains the cleanest way to isolate an entire multi-task run
from work you are doing in parallel.

## Isolating a One-Off `exec` Run

Concurrent writing runs must not share a checkout: two agents editing one
directory interleave their edits, and neither result is reviewable. Give each
writing run its own working copy and pass it with the global `-C` option
(`lemming -C <working-copy> -v exec ...`). The full Git worktree recipe is under
"Isolate concurrent writing runs" in [SKILL.md](../SKILL.md), summarized in the
[README](../README.md#one-off-tasks-without-a-roadmap).

## Isolating a Roadmap Loop

`lemming run` executes its tasks **sequentially in one shared workspace**. Task
2 starts in whatever state task 1 left behind — that is the point, since tasks
build on each other. So the unit of isolation is the whole run: **one branch or
worktree for the entire loop**, created once before `lemming run`.

**Never one branch per task.** Whichever branch the last runner left checked out
would silently become the next task's baseline, and the loop's history would
fragment across branches nobody asked for. This is why the task-runner prompt
says nothing about branching: a runner has no business changing what is checked
out under it.

The minimal Git shape, from a clean tree:

```bash
repo=$(git rev-parse --show-toplevel) || exit 1
test -z "$(git -C "$repo" status --porcelain)" || exit 1

# One worktree for the whole run, from committed HEAD.
worktree=$(mktemp -d "${TMPDIR:-/tmp}/lemming-run.XXXXXX") || exit 1
branch="lemming/$(basename "$worktree")"
git -C "$repo" worktree add -b "$branch" "$worktree" HEAD || exit 1

# Drive the loop against it; -C is a global option, before the subcommand.
lemming -C "$worktree" goal "<the long-term goal>"
lemming -C "$worktree" add "<first task>"
lemming -C "$worktree" -v run
```

A plain branch (`git switch -c lemming/<run>`) does just as well when nothing
else needs the checkout; the worktree only buys you the original directory to
keep working in meanwhile. Either way the roadmap travels with the workspace: a
tracked `tasks.yml` is copied into the worktree and the loop's edits stay there,
and without one Lemming keeps isolated state keyed on the worktree path.

## Fanning Out Parallel Worktrees

Worktree isolation stops concurrent runners from interleaving edits on disk, not
from colliding when their branches merge. Only fan out genuinely file-disjoint
streams: never parallelize tasks that touch the same files, shared schemas or
types, central router or CLI registrations, or lockfiles. Land shared
foundations on the base branch and commit them before branching any worktrees.

From a clean checkout, create one worktree and `lemming/<slug>` branch per
stream from committed `HEAD`, seed each roadmap, and start detached loops (for a
single-shot stream, either queue one `--oneshot` task under `run --detach` so
the combined waiter below tracks it, or run `lemming -C "$wt" -v exec ...` when
a dedicated caller or subagent owns that stream):

```bash
repo=$(git rev-parse --show-toplevel) || exit 1
test -z "$(git -C "$repo" status --porcelain)" || exit 1
wt_root=$(mktemp -d "${TMPDIR:-/tmp}/lemming-fanout.XXXXXX") || exit 1

wts=""
for slug in auth-events billing-csv; do
  wt="$wt_root/$slug"
  git -C "$repo" worktree add -b "lemming/$slug" "$wt" HEAD || exit 1
  wts="${wts:+$wts }$wt"
done

lemming -C "$wt_root/auth-events" goal "Emit audit events in src/auth/"
lemming -C "$wt_root/auth-events" add "Record login and token refresh events"
lemming -C "$wt_root/auth-events" run --detach

lemming -C "$wt_root/billing-csv" goal "Export invoices in src/billing/"
lemming -C "$wt_root/billing-csv" add "Stream CSV rows with RFC 4180 quoting"
lemming -C "$wt_root/billing-csv" run --detach
```

When `tasks.yml` is untracked, Lemming keys each roadmap by its resolved
worktree path under `~/.local/lemming/`, so parallel loops share no state and
dirty no tracked files. If the repository tracks `tasks.yml`, every loop mutates
its worktree's copy and the branches will conflict on merge — untrack
`tasks.yml` before branching, or discard or reconcile those edits in each
worktree before committing.

Supervise all detached loops with **one** combined waiter instead of holding `N`
open background shells or polling loops that fill a supervising agent's context
window. The waiter snapshots `status --brief` across `$wts` and sleeps until
_any_ worktree changes (a task settles, turns `Stale`, or fails, or a loop
becomes `Idle`):

```sh
before=$(for wt in $wts; do lemming -C "$wt" status --brief; done)
timeout 60m sh -c '
  wts=$2
  while [ "$(
    for wt in $wts; do lemming -C "$wt" status --brief; done
  )" = "$1" ]; do
    sleep 60
  done
' _ "$before" "$wts"
```

Clean merges do not guarantee semantic compatibility across streams. As streams
finish, land and verify them **one branch at a time**:

1. Inspect `lemming -C "$wt" diff --stat` (or `diff <id> --stat` per task) and
   `git -C "$wt" status --short`. Verify any commits the runner made, and commit
   remaining intended edits on `lemming/<slug>` while excluding untracked
   throwaway state (and restoring or reconciling any tracked `tasks.yml`).
2. Merge into the integration branch one stream at a time and run the test suite
   (plus an optional scoped review) on the integrated tree before landing the
   next stream, so cross-branch interactions are caught immediately:

   ```bash
   git -C "$repo" merge --no-ff "lemming/$slug"
   # Run the project's test suite on $repo, and optionally review the merge:
   git -C "$repo" diff -z --name-only --diff-filter=d HEAD~1..HEAD |
     lemming -C "$repo" exec --review testing --scope -
   ```

3. Remove the landed worktree and branch non-forcibly, drop `$wt` from `$wts`
   (`-C` requires an existing directory), and re-arm the waiter until every
   stream has merged:

   ```bash
   git -C "$repo" worktree remove "$wt"
   git -C "$repo" branch -d "lemming/$slug"
   ```

## Inspecting Diffs and Rolling Back Tasks

Every snapshotted task records its `pre_tree`, `runner_tree`, `post_tree`, and
per-hook tree pairs (`hook_trees`), so you can inspect or undo individual tasks
and hooks without relying on the runner having made Git commits:

```bash
lemming -C "$worktree" status                 # queue, attempts, diff summaries
lemming -C "$worktree" diff <id>              # full patch for one task
lemming -C "$worktree" diff <id> --stat       # diffstat summary
lemming -C "$worktree" diff <id> --hook <name> # changes made by one hook
lemming -C "$worktree" logs <id>              # full runner and hook log
```

When the orchestrator loop is idle and the workspace has no uncommitted manual
edits outside the latest snapshot, `lemming rollback` can revert completed work
at three granularities:

```bash
# Rewind to <id>'s pre_tree and reset <id> and later tasks to pending
lemming -C "$worktree" rollback <id>

# Revert only <id>'s patch in place (3-way reverse apply) and reset only <id>
lemming -C "$worktree" rollback <id> --only

# Revert only one hook's changes on <id>, keeping the task completed
lemming -C "$worktree" rollback <id> --hook testing
```

If you also created a dedicated worktree or branch before the run, you still
have standard Git commands (`git status`, `git log HEAD`, `git diff`) to review
or discard the entire branch wholesale.

Runners are also told to write verbose evidence into Lemming's per-project
scratch directory rather than into the workspace, so a failed task's notes and
`attempt-<N>.patch` artifacts survive even after a task is rolled back or you
throw the branch away. It sits under `~/.local/lemming/` (or `$LEMMING_HOME`),
named for the first 12 hex digits of the SHA-256 of the resolved tasks-file path
— or of the workspace path, when Lemming is keeping the roadmap itself. Once the
worktree is gone `-C` can no longer resolve it, and
`lemming --tasks-file <that dir>/tasks.yml logs <id>` is the way back in.

Retain a worktree until its changes have been recovered, then clean up
non-forcibly, which refuses rather than destroying uncommitted work — including
a tracked `tasks.yml` the loop has edited, which must be committed or discarded
first:

```bash
git -C "$repo" worktree remove "$worktree"
git -C "$repo" branch -d "$branch"
```

## What the Runner Prompt and Snapshots Guarantee

Runners are instructed to leave a coherent workspace, to back out their own
half-applied edits before failing, and never to restore files from version
control history; the rules live in the "Workspace Hygiene" directive of
`src/lemming/prompts/taskrunner.md`, not in a copy here. Shadow Git snapshots
back that guidance up mechanically: when an attempt fails, times out, crashes,
or is cancelled, or when a post-run hook fails, Lemming restores the workspace
to the pre-attempt or pre-hook tree automatically.

## Workspaces Not Under Version Control

Shadow Git snapshots work in a plain directory that has no `.git` of its own, as
long as the `git` binary is installed on the machine. In a greenfield directory,
create a `.gitignore` before tasks install dependencies or run builds so
snapshots skip `node_modules/`, `.venv/`, and build outputs.

If `git` is not installed on the host, snapshots are disabled silently and there
is nothing to diff against or reset; **copy the directory before the run** if
you need a rollback baseline:

```bash
cp -a project project.bak
lemming -C project run
```
