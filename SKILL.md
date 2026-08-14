---
name: lemming
description: One interface over the AI coding agent CLIs (agy, claude, codex). Delegate a single task to another agent to spare a quota or play to its strengths; run a readability, testing, or UX review over changed files, a branch, or a path; or drive a multi-task project through a roadmap that survives context limits. Use whenever work should run under a different agent than the current one, when reviewing a diff before opening a pull request, or when a job is too large for one agent run.
license: MIT
---

# lemming

One interface over several agent CLIs. You do not need to know how `agy`,
`claude`, or `codex` spell their flags — only `lemming`.

Use `exec` for one unit of work, and a roadmap for anything larger than a
single agent run can hold.

## Delegate one task

```sh
lemming -v exec "Fix the flaky heartbeat test in src/runner_test.py" --runner codex

# Pipe a longer handoff rather than fighting shell quoting; no length limit
cat handoff.md | lemming -v exec -f - --runner agy
```

The agent's closing message comes back on **stdout**, everything else on
stderr, and the exit code is 0 only if the task completed. Read that message
instead of hunting through logs.

When you launch `exec` as a subprocess, use the global `-v` flag (before
`exec`) and continue monitoring its output until it exits. Verbose mode streams
the runner's activity on stderr. At startup, stderr also reports the exact
`Tasks file` and `Task ID`; use them from another process to inspect the
agent's durable findings or full log without guessing which `exec-*` directory
belongs to the run:

```sh
lemming --tasks-file <reported-path> status <reported-task-id>
lemming --tasks-file <reported-path> logs <reported-task-id>
```

The task runner is required to record its approach first and concise findings
as it works. These entries are checkpoints rather than a continuous trace, so
use verbose output or the log to tell whether it is actively making progress.

The task starts with an empty context and sees nothing of your conversation,
so give it everything it needs.

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
which is resolved to the files it changed. It defaults to uncommitted work,
and a clean tree stops the run rather than reviewing everything.

Reviews **edit the workspace** — readability applies fixes and reruns checks.
That is the point, but it means a review is not read-only.

## Drive a roadmap

For work that outlasts one agent run — a migration, a multi-step feature —
break it into tasks. Each runs with a fresh context seeded only by the goal
and recorded progress, so the project survives context limits.

```sh
lemming goal "Add offline support with a service worker and a sync queue"
lemming add "Register the service worker and cache the app shell"
lemming add "Queue writes in IndexedDB while offline"
lemming run                                  # runs until the queue drains
```

Check on it, and read what an agent actually did:

```sh
lemming status              # queue, attempts, and recorded progress
lemming status <id>         # one task in detail, including its runner command
lemming logs                # the active task's log, or logs <id> for one task
```

Adjust it while it runs — new tasks are picked up automatically:

```sh
lemming add "Add a retry backoff to the sync queue" --index 0  # jump the queue
lemming stop --after-current-task                             # drain, then stop
```

After each task, review hooks run automatically and a roadmap hook may revise
the queue. `lemming hooks list` shows them; `lemming hooks disable <name>`
turns one off for the project.

## What to know before running any of it

- **The agent runs unattended.** `--yolo` is the default, so it does not
  inherit the permission prompts of whatever launched it. Treat this as
  granting an agent unsupervised write access to the working directory.
- **`exec` makes one attempt, no retry.** A failure is final. A roadmap task
  retries (3 by default) with its progress carried forward.
- **Interrupting leaves partial edits.** Nothing is restored, so there is no
  atomicity to rely on.
- **Concurrent runs in one checkout interleave their edits.** Give each
  writing run its own worktree and address it with `-C`.
- **Every run reports its state.** The tasks file and task ID are printed on
  stderr at startup. Successful state is removed on exit unless `--keep` is
  used; failures keep their directory and print it again for recovery. Kept
  failure state is retired automatically after a week.
- **`--tasks-file` and `-C` are group options.** They go before the
  subcommand — `lemming -C ../other exec ...`, never `exec -C ../other`.

`lemming --help`, or `--help` on any subcommand, has the full flag list.
