# Workspace Isolation 🧰

## Lemming is VCS-Agnostic

An agent run writes to a real directory on your disk. The orchestrator never
creates a branch, a commit, or a worktree, and never reverts anything: it runs
the agent in a directory and gets out of the way, so whatever a run leaves
behind — finished work, half-applied edits, scratch files — is what you get. The
task-runner prompt does allow the agent itself to commit when that is the
project's convention, but the orchestrator neither asks for that nor relies on
it.

This is deliberate: branching would tie the orchestrator to Git, and Lemming is
expected to work in a Mercurial repository, a Perforce client, or a plain
directory under no version control at all. No portable "undo the last run"
primitive exists across those, so **isolation and recovery are the caller's
job**. If you need to be able to throw a run away, set that up before it starts.

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

## Recovering After an Aborted Run

With a pre-run baseline, recovery is ordinary version control, and `-C` reaches
the run's own records without your having to know where they are stored:

```bash
git -C "$worktree" status        # what the run touched
git -C "$worktree" log HEAD      # commits, if the runners made any
git -C "$worktree" diff          # whatever is still uncommitted

lemming -C "$worktree" status    # queue, attempts, recorded progress
lemming -C "$worktree" logs <id> # the full runner log for one task
```

Keep the branch, or discard it wholesale. There is no partial rollback of one
task inside a loop — the tasks share a workspace, so their edits are not
separable unless the runners committed them separately.

Runners are also told to write verbose evidence into Lemming's per-project
scratch directory rather than into the workspace, so a failed task's notes
survive even after you throw the branch away. It sits under `~/.local/lemming/`
(or `$LEMMING_HOME`), named for the first 12 hex digits of the SHA-256 of the
resolved tasks-file path — or of the workspace path, when Lemming is keeping the
roadmap itself. Once the worktree is gone `-C` can no longer resolve it, and
`lemming --tasks-file <that dir>/tasks.yml logs <id>` is the way back in.

So retain the worktree until its changes have been recovered, then clean up
non-forcibly, which refuses rather than destroying uncommitted work — including
a tracked `tasks.yml` the loop has edited, which must be committed or discarded
first:

```bash
git -C "$repo" worktree remove "$worktree"
git -C "$repo" branch -d "$branch"
```

## What the Runner Prompt Guarantees

Runners are instructed to leave a coherent workspace, to back out their own
half-applied edits before failing, and never to restore files from version
control history; the rules live in the "Workspace Hygiene" directive of
`src/lemming/prompts/taskrunner.md`, not in a copy here. Treat them as a strong
prior, not a guarantee: runners are LLMs, a prompt is guidance rather than
enforcement, and a run killed by a timeout never gets to clean up at all. The
branch or worktree is what actually makes a bad run recoverable; the prompt only
reduces how often you need it.

## Workspaces Not Under Version Control

If the workspace is a plain directory, there is nothing to diff against and
nothing to reset. **Copy the directory before the run**; that copy is the only
rollback available.

```bash
cp -a project project.bak
lemming -C project run
```
