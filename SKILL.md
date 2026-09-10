---
name: lemming
description:
  One interface over the AI coding agent CLIs (agy, claude, codex, opencode).
  Delegate a single task to another agent to spare a quota or play to its
  strengths; run a readability, testing, or UX review over changed files, a
  branch, or a path; or drive a multi-task project through a roadmap that
  survives context limits. Use whenever work should run under a different agent
  than the current one, when reviewing a diff before opening a pull request, or
  when a job is too large for one agent run.
license: MIT
---

# lemming

One interface over several agent CLIs. You do not need to know how `agy`,
`claude`, `codex`, or `opencode` spell their flags — only `lemming`.

Use `exec` for one unit of work, and a roadmap for anything larger than a single
agent run can hold.

## Delegate one task

```sh
lemming -v exec "Fix the flaky heartbeat test in src/runner_test.py" --runner codex

# Opt into at most three total attempts for transient runner failures
lemming -v exec "Fix the flaky heartbeat test" --retries 3

# Pipe a longer handoff rather than fighting shell quoting; no length limit
cat handoff.md | lemming -v exec -f - --runner agy
```

The agent's closing message comes back on **stdout**, everything else on stderr,
and the exit code is 0 only if the task completed. Read that message instead of
hunting through logs.

When you launch `exec` as a subprocess, use the global `-v` flag (before `exec`)
and continue monitoring its output until it exits. Verbose mode streams the
runner's activity on stderr. At startup, stderr also reports the exact
`Tasks file` and `Task ID`; use them from another process to inspect the agent's
durable findings or full log without guessing which `exec-*` directory belongs
to the run:

```sh
lemming --tasks-file <reported-path> status <reported-task-id>
lemming --tasks-file <reported-path> logs <reported-task-id>
```

The task runner is required to record its approach first and concise findings as
it works. These entries are checkpoints rather than a continuous trace, so use
verbose output or the log to tell whether it is actively making progress.

The runner receives no conversation history and cannot be steered mid-run. Give
it a self-contained handoff with:

- **Goal:** the concrete outcome to produce.
- **Hard constraints:** scope, compatibility, process, and safety requirements.
- **Relevant findings and evidence:** paths, commands, output, and prior
  observations it should rely on.
- **Approaches ruled out and why:** decisions it should not revisit without new
  evidence.
- **Definition of done and verification:** the required result and checks that
  prove it works.

For example:

```sh
lemming -v exec -f - --runner codex <<'EOF'
# Goal
Fix the race that lets two workers claim the same queued job.

# Hard constraints
- Keep the SQLite schema and public CLI unchanged.
- Make the smallest scoped change; do not reformat unrelated files.

# Relevant findings and evidence
- `uv run pytest tests/test_queue.py -k concurrent_claim` fails intermittently.
- `src/queue.py:claim_next` reads and updates in separate transactions.

# Approaches ruled out and why
- Do not add an in-process mutex; separate worker processes would bypass it.

# Definition of done and verification
- The concurrency test passes repeatedly and no existing behavior regresses.
- Run the focused test, then the full test suite and configured linter.
EOF
```

## Isolate concurrent writing runs

Concurrent writing runs must use separate working copies. Detect and use the
repository's existing version control system, then pass each copy with the
global `-C` option. The caller owns creation, result recovery, and cleanup;
Lemming remains independent of Git, Mercurial, and other VCSs.

For Git, refuse modified or untracked input rather than silently excluding it,
then create a worktree from committed `HEAD`:

```sh
repo=$(git rev-parse --show-toplevel) || exit 1
test -z "$(git -C "$repo" status --porcelain)" || exit 1
worktree=$(mktemp -d "${TMPDIR:-/tmp}/lemming-exec.XXXXXX") || exit 1
branch="lemming/$(basename "$worktree")"
git -C "$repo" worktree add -b "$branch" "$worktree" HEAD || exit 1
printf 'Worktree: %s\nBranch: %s\n' "$worktree" "$branch"
lemming -C "$worktree" -v exec "<self-contained task>" --runner codex
```

Retain the reported worktree after success, failure, timeout, or interruption.
After its changes have been committed, copied, or deliberately discarded, use
Git's non-forcing cleanup so dirty or unmerged work is not destroyed:

```sh
git -C "$repo" worktree remove "$worktree"
git -C "$repo" branch -d "$branch"
```

## Run a review

With no description there is nothing for a task runner to do, so only the
reviews run — against work that already exists.

```sh
lemming exec --review readability            # uncommitted work
lemming exec --review testing --scope main...HEAD
lemming exec --review all --scope src/api/
lemming exec "Add pagination" --review all   # do the work, then gate it

# Someone else's branch, in a worktree so your own tree is untouched
git worktree add /tmp/pr-123 && (cd /tmp/pr-123 && gh pr checkout 123)
lemming -C /tmp/pr-123 exec --review testing --scope main...HEAD
```

`--scope` takes paths, which pass through untouched, or a git revision range,
which is resolved to the files it changed. It defaults to uncommitted work, and
a clean tree stops the run rather than reviewing everything.

Reviews may edit the working directory. Readability intentionally applies fixes,
and testing may repair production code or tests. UX is instructed to remain
advisory, but its prompt and `--no-yolo` do not enforce filesystem isolation. A
global review override may perform any actions its prompt requests. When
isolation is required, use a separate VCS-managed working copy and pass it with
the global `-C` option, as in the example above.

## Drive a roadmap

For work that outlasts one agent run — a migration, a multi-step feature — break
it into tasks. Each runs with a fresh context seeded only by the goal and
recorded progress, so the project survives context limits.

```sh
lemming goal "Add offline support with a service worker and a sync queue"
lemming add "Register the service worker and cache the app shell"
lemming add "Queue writes in IndexedDB while offline"
lemming add "Bump the service worker cache version" --oneshot
lemming run                                  # runs until the queue drains
lemming run --until <task-id>                # or stops once that task settles
```

The loop shares one workspace across all its tasks, so isolate the **whole run**
— one branch or worktree created before `lemming run`, driven with
`lemming -C <worktree> run` — never one per task.

**Mark mechanical tasks `--oneshot`.** After every other task, review hooks run
and a roadmap hook may revise the queue; each hook is a full agent run, so a
typo fix, rename, version bump, or cleanup without `--oneshot` takes several
times longer than the work itself. Reserve hooked tasks for changes in
behavior. `lemming hooks list` shows the hooks; `lemming hooks disable <name>`
turns one off for the project.

### Supervise in milestones

Never run the whole roadmap and poll it from one context. Every `status` call
lands in your context window, and the run outlives it. Split the queue into
milestones instead:

1. Queue the whole roadmap, pick the task that ends the first milestone, and
   run `lemming run --until <task-id>`. It blocks, runs anything the roadmap
   hook inserts ahead of that task, follows the task through any split, and
   exits printing `Reached milestone <id>; M pending.` or
   `All tasks completed!`. A non-zero exit means a task failed, the queue is
   blocked, or the milestone task was deleted; `lemming status --brief` shows
   which. Add `--max-tasks N` as a budget cap when a runaway roadmap hook
   would be expensive.
2. Between milestones, judge direction: `lemming status --brief`, then
   `lemming status <id>` for the tasks that matter. Re-plan with `add`, `edit`,
   `supersede`, or `delete`, then run the next milestone.
3. Delegate each milestone to a subagent when one exists, so the polling and
   the log reading stay out of your context. The subagent reports back in the
   fixed shape below, nothing more.

This is how a stronger model directs cheaper ones: `lemming config set runner`
and `config set model` pick the worker; the supervising agent only reads
status between milestones and decides what happens next.

Checking on a running milestone is fine when a task might be stuck, but poll
sparingly and read the smallest thing that answers the question:

```sh
lemming status --brief      # one row per task, no descriptions
lemming status <id>         # Run Time against the time limit, progress entries
lemming logs <id> | tail -n 30   # never the whole log
```

A task is stuck when its run time approaches the limit with no new progress
entries and the log tail shows the same step repeating. Otherwise leave it.

Adjust the queue while it runs — new tasks are picked up automatically:

```sh
lemming add "Add a retry backoff to the sync queue" --index 0  # jump the queue
lemming stop --after-current-task                             # drain, then stop
```

### Report tersely

Your own words fill context faster than any tool. Whether reporting to a user or
to a parent agent:

- Report one milestone in this shape and no other: outcome (`done`, `failed`,
  `blocked`), task IDs that settled, files changed, one line of guidance for the
  next milestone. Skip anything that can be re-read from `lemming status`.
- Never paste `status`, `logs`, test output, or diffs into a message or a
  progress entry; name the task ID or file and let the reader fetch it.
- Progress entries are one line each. Evidence goes into
  `lemming artifact <id> <name> --file -`, not the entry.

### Do not nest

Never call `lemming run` or `lemming exec` from inside a task runner or a hook.
Each level spawns another full agent against the same quota, and the inner loop
competes with the outer one for the same tasks file. A runner that needs more
work done adds tasks with `lemming add`; the running loop picks them up.

## What to know before running any of it

- **The agent runs unattended.** `--yolo` is the default, so it does not inherit
  the permission prompts of whatever launched it. Treat this as granting an
  agent unsupervised write access to the working directory.
- **`exec` makes one attempt by default.** `--retries N` opts into at most `N`
  total task attempts with progress carried forward. Explicit failure,
  cancellation, interruption, and failed reviews stop immediately.
- **Interrupting leaves partial edits.** Nothing is restored, so there is no
  atomicity to rely on.
- **Concurrent runs in one checkout interleave their edits.** Give each writing
  run its own VCS-managed working copy and address it with `-C`; retain the copy
  until its result is recovered.
- **Every run reports its state.** The tasks file and task ID are printed on
  stderr at startup. Successful state is removed on exit unless `--keep` is
  used; failures keep their directory and print it again for recovery. Kept
  failure state is retired automatically after a week.
- **`--tasks-file` and `-C` are group options.** They go before the subcommand —
  `lemming -C ../other exec ...`, never `exec -C ../other`.

`lemming --help`, or `--help` on any subcommand, has the full flag list.
