---
name: lemming
description: Delegate a single coding task to another AI agent CLI (agy, claude, codex) through one interface, or run a code review — readability, testing, UX — over changed files, a PR branch, or a path. Use when handing work to a different agent to spare a quota or play to its strengths, or when reviewing a diff before opening a pull request. Not for driving a multi-task project; that is a roadmap.
license: MIT
---

# lemming

One interface over several agent CLIs. You do not need to know how `agy`,
`claude`, or `codex` spell their flags — only `lemming exec`.

## Delegate one task

```sh
lemming exec "Fix the flaky heartbeat test in src/runner_test.py" --runner codex
```

The agent's closing message comes back on **stdout**; progress and the event
trace go to stderr. Exit code is 0 only if the task completed. That message is
the return value — read it instead of hunting through logs.

Pipe a longer handoff rather than fighting shell quoting. There is no length
limit, unlike a roadmap task description:

```sh
cat handoff.md | lemming exec -f - --runner agy
```

Delegate when another agent's quota, price, or strengths suit the work better
than yours. Give the task everything it needs: it starts with an empty context
and sees nothing of your conversation.

## Run a review

With no description there is nothing for a task runner to do, so only the
reviews run — against work that already exists.

```sh
lemming exec --review readability          # uncommitted work
lemming exec --review testing --scope main...HEAD
lemming exec --review all --scope src/api/
lemming exec "Add pagination" --review all # do the work, then gate it
```

`--scope` takes paths, which pass through untouched, or a git revision range,
which is resolved to the files it changed. It defaults to uncommitted work.
A clean tree stops the run rather than reviewing everything.

Reviews **edit the workspace**: readability applies fixes and reruns checks.
That is the point — but it means a review is not read-only.

## Reviewing someone else's branch

Check it out in a worktree so your own tree is untouched, and point `-C` at it:

```sh
git worktree add /tmp/pr-123 && (cd /tmp/pr-123 && gh pr checkout 123)
lemming -C /tmp/pr-123 exec --review testing --scope main...HEAD
```

## What to know before running it

- **The agent runs unattended.** `--yolo` is the default, so it does not
  inherit the permission prompts of whatever launched it. Treat `lemming exec`
  as granting an agent unsupervised write access to the working directory.
- **One agent run, no retry.** A failure is final; it does not silently try
  again. `--time-limit` caps the wall clock (default 60 minutes).
- **Interrupting leaves partial edits.** The workspace is not restored, so
  there is no atomicity to rely on.
- **Failures keep their log.** The state directory path is printed on stderr;
  read it with `lemming logs` pointed at that tasks file.

## Running several at once

Concurrent runs in one checkout will interleave their edits. Give each write
task its own worktree and address it with `-C`. Read-only work parallelizes
safely as-is.

## When not to use it

`exec` is for one unit of work. Anything needing more context than a single
agent run can hold — a migration across a large codebase, a multi-step feature
— belongs on a roadmap: `lemming add`, then `lemming run`. In particular,
`--scope .` over a large repository *samples*; it does not cover.

Run `lemming exec --help` for the full flag list.
