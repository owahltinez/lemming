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

```sh
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

A fresh Git worktree contains only tracked files from `HEAD`. It does **not**
inherit untracked state from `$repo`:

- **Project configuration**: `lemming config` settings (`runner`, `model`,
  `retries`, `time_limit`, enabled hooks) stored in an untracked roadmap start
  at defaults; pass `--runner` / `--model` per task or run
  `lemming -C "$wt" config set ...` per worktree.
- **Environment and local hooks**: copy or symlink `.env` and any untracked
  `.lemming/hooks` into `$wt` when needed (global `~/.local/lemming/.env` and
  `~/.local/lemming/hooks` still apply automatically).
- **Dependencies**: untracked environments (`.venv`, `node_modules`) are absent
  until installed or linked in `$wt`.
- **Roadmaps (`tasks.yml`)**: when untracked, Lemming hashes each resolved
  worktree path to `~/.local/lemming/<hash>/tasks.yml` automatically (remove
  that state directory afterward if discarding a throwaway run). If the
  repository tracks `tasks.yml`, every loop mutates its worktree's copy and must
  be restored before committing so branches do not conflict on merge.

Record `base` from a clean checkout, create one worktree and `lemming/<slug>`
branch per stream under `$wt_root`, seed each roadmap, and start detached loops
(for a single-shot stream, either queue one `--oneshot` task under
`run --detach` so the waiter below tracks it, or run
`lemming -C "$wt" -v exec ...` when a dedicated caller or subagent owns that
stream):

```sh
repo=$(git rev-parse --show-toplevel) || exit 1
test -z "$(git -C "$repo" status --porcelain)" || exit 1
base=$(git -C "$repo" rev-parse HEAD) || exit 1
wt_root=$(mktemp -d "${TMPDIR:-/tmp}/lemming.XXXXXX") || exit 1

for slug in auth-events billing-csv; do
  git -C "$repo" worktree add -b "lemming/$slug" "$wt_root/$slug" "$base" || exit 1
done

lemming -C "$wt_root/auth-events" goal "Emit audit events in src/auth/"
lemming -C "$wt_root/auth-events" add "Record login and refresh events" --runner codex
lemming -C "$wt_root/auth-events" run --detach

lemming -C "$wt_root/billing-csv" goal "Export invoices in src/billing/"
lemming -C "$wt_root/billing-csv" add "Stream CSV rows with RFC 4180 quoting" --runner codex
lemming -C "$wt_root/billing-csv" run --detach
```

Supervise all detached loops from **one** combined waiter instead of holding `N`
background shells or polling loops in context. Iterating `"$wt_root"/*/` handles
spaces in `$TMPDIR` and shrinks automatically as landed worktrees are removed;
the waiter exits `0` immediately if any remaining stream is already `Idle` or
`Stale`, propagates CLI errors, and waits out initial task claiming (`[ ]` to
`[*]`) before comparing snapshots:

```sh
timeout 60m sh -c '
  wt_root=$1; interval=${2:-30}
  poll() {
    snap=""; claiming=0
    for wt in "$wt_root"/*/; do
      [ -d "$wt" ] || continue
      out=$(lemming -C "$wt" status --brief) || exit 1
      case "$out" in
        *"Loop Status: Idle"* | *"    Stale: "*) exit 0 ;;
        *"[*] "*) ;; *) claiming=1 ;;
      esac
      snap="$snap$wt:$out
"
    done
    [ -n "$snap" ] || exit 0
  }
  for _ in 1 2 3 4 5; do poll; [ "$claiming" -eq 0 ] && break; sleep 1; done
  before=$snap
  while :; do sleep "$interval"; poll; [ "$snap" != "$before" ] && exit 0; done
' _ "$wt_root"
```

Clean merges do not guarantee semantic compatibility across streams. As streams
finish, land and verify them **one branch at a time**, running mutating reviews
only once after all merges so `$repo` stays clean between merges:

```sh
for slug in auth-events billing-csv; do
  wt="$wt_root/$slug"
  branch="lemming/$slug"
  [ -d "$wt" ] || continue

  # Inspect every task (`lemming diff` alone shows only the latest) plus $base diff.
  lemming -C "$wt" -v status
  git -C "$wt" status --short
  git -C "$wt" diff --stat "$base"

  # Restore a tracked tasks.yml and drop its lockfile so branches do not conflict.
  if git -C "$wt" ls-files --error-unmatch tasks.yml >/dev/null 2>&1; then
    git -C "$wt" checkout -- tasks.yml && rm -f "$wt/tasks.lock"
  fi

  # Remove throwaway files first; commit uncommitted edits and reject no-op branches.
  if [ -n "$(git -C "$wt" status --porcelain)" ]; then
    git -C "$wt" add -A && git -C "$wt" commit -m "Land $slug" || exit 1
  fi
  [ "$(git -C "$wt" rev-parse HEAD)" != "$base" ] || exit 1

  git -C "$repo" merge --no-ff "$branch" || exit 1
  # Run the project's non-mutating test suite on $repo before the next merge.

  git -C "$repo" worktree remove "$wt" || exit 1
  git -C "$repo" branch -d "$branch" || exit 1
done
rmdir "$wt_root"

# Guard against pure-deletion diffs where --scope - would receive no paths.
if [ -n "$(git -C "$repo" diff --name-only --diff-filter=d "$base"..HEAD)" ]; then
  git -C "$repo" diff -z --name-only --diff-filter=d "$base"..HEAD |
    lemming -C "$repo" exec --review testing --scope -
fi
```

## Inspecting Diffs and Rolling Back Tasks

Every snapshotted task records its `pre_tree`, `runner_tree`, `post_tree`, and
per-hook tree pairs (`hook_trees`), so you can inspect or undo individual tasks
and hooks without relying on the runner having made Git commits:

```sh
lemming -C "$worktree" status                 # queue, attempts, diff summaries
lemming -C "$worktree" diff <id>              # full patch for one task
lemming -C "$worktree" diff <id> --stat       # diffstat summary
lemming -C "$worktree" diff <id> --hook <name> # changes made by one hook
lemming -C "$worktree" logs <id>              # full runner and hook log
```

When the orchestrator loop is idle and the workspace has no uncommitted manual
edits outside the latest snapshot, `lemming rollback` can revert completed work
at three granularities:

```sh
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

```sh
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

```sh
cp -a project project.bak
lemming -C project run
```
