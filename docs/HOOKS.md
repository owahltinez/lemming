# Orchestrator Hooks ⚓️

Orchestrator hooks are a powerful way to extend the Lemming workflow. They allow
you to execute custom agents or scripts after each task, enabling automated
roadmap revision, code quality checks, or any other post-task validation.

## How it Works

When a task finishes (whether it succeeded or failed), Lemming can run one or
more **orchestrator hooks**. Each hook:

1.  **Loads a prompt template**: Lemming looks for a `.md` file corresponding to
    the hook name.
2.  **Renders the prompt**: It injects context about the current roadmap and the
    task that just finished.
3.  **Invokes an agent**: It runs your configured runner with the rendered
    prompt.
4.  **Captures the output**: The agent's output is appended to the task's
    execution log, and any commands it runs (e.g. `lemming add`, `lemming edit`)
    are executed against the roadmap.

## Naming Convention

Hooks are Markdown files discovered from the filesystem, and udev-style
filename conventions control their behavior:

- **Ordering**: A numeric prefix determines execution order: `10-lint.md`
  runs before `90-roadmap.md`. Files without a prefix default to priority
  50. The prefix is **not** part of the hook's name: `90-roadmap.md`
  defines the hook `roadmap`.
- **Failure hooks**: Hooks at priority 90 and above also run when a task
  fails. All other hooks only run after successful tasks. The built-in
  `roadmap` hook ships as `90-roadmap.md` so it can react to failures.
- **Masking**: An empty file disables the hook of the same name from a
  lower-precedence layer (see below). For example, an empty
  `.lemming/hooks/readability.md` disables the built-in `readability` hook
  for that project.
- **Overriding**: A non-empty file replaces the hook of the same name from
  a lower-precedence layer. The winning filename also determines the
  priority, so keep the `9x-` prefix when overriding a failure hook (e.g.
  `90-roadmap.md`), or use a different prefix to deliberately reorder it.

## Built-in Hooks

### `roadmap` (Default)

The primary mechanism for autonomous project management. It analyzes the results
of the finished task and decides if the remaining roadmap needs to be adjusted
(e.g., adding a missing prerequisite, skipping obsolete tasks, or breaking down
a broad task).

### `readability`

A code quality hook that reviews changes for adherence to the Google Style Guide
and general readability using the bundled `lemming readability` tool. It
provides feedback via task progress or suggests follow-up refactoring tasks.

### Editing Built-in Hook Prompts

The built-in hook prompts are load-bearing: a wording change can regress
orchestration behavior without any unit test failing. When you edit a prompt
under `src/lemming/prompts/hooks/`, run its eval suite before shipping:

```bash
uv run python -m lemming.evals run --suite roadmap
uv run python -m lemming.evals run --suite readability
```

See [EVALS.md](EVALS.md) for how the suites work and how to interpret results.

## Custom Hooks

You can define your own hooks by creating Markdown files in the following
locations:

1.  `.lemming/hooks/{name}.md` (Project-specific hooks)
2.  `~/.local/lemming/hooks/{name}.md` (Global hooks)

### Precedence

When Lemming looks for a hook, it resolves each logical name through the
following layers, highest precedence first:

1.  **Project-specific**: `.lemming/hooks/`
2.  **Global**: `~/.local/lemming/hooks/`
3.  **Built-in**: Bundled with the Lemming package.

Because of this precedence order, overriding a built-in hook only requires
creating a Markdown file with the same logical name in the project or global
directory (e.g. `~/.local/lemming/hooks/90-roadmap.md`); delete the file to
restore the built-in version. Global hooks are available to all Lemming
projects on the system.

### Example: `lint` hook

Create `.lemming/hooks/lint.md`:

```markdown
You are a code quality orchestrator. A task has just finished. Review the files
changed in this task and ensure they follow the project's style guide. If there
are issues, add a new task to fix them.

### Roadmap Context

{{roadmap}}

### Finished Task

{{finished_task}}
```

## Using Hooks

### Enabling and Disabling

Lemming runs every hook it discovers that is not masked. There is no hook
configuration in `tasks.yml`; the filesystem is the single source of truth.

The `hooks` command group provides shortcuts for managing project masks:

```bash
# Disable a hook for this project (writes an empty .lemming/hooks/50-lint.md)
lemming hooks disable lint

# Re-enable it (removes the mask file)
lemming hooks enable lint
```

These commands validate the hook names before changing anything and refuse
to touch files with content (a non-empty file is an override, not a mask).
An empty file created by hand works just as well; the commands additionally
keep the hook's priority in the mask filename so listings stay accurate.

Hooks are re-discovered on every task execution, so changes are picked up
dynamically by a running orchestrator loop.

### Available Variables

Your hook template can use the following placeholders:

- `{{roadmap}}`: A structured summary of the long-term goal and all tasks.
- `{{finished_task}}`: Details about the task that just finished (ID,
  description, progress, and the last 100 lines of its execution log).
- `{{finished_task_id}}`: The ID of the task that just finished.
- `{{tasks_file_name}}`: The filename of the tasks YAML file.
- `{{tasks_file_path}}`: The full path to the tasks YAML file.

## Developer Ergonomics

### Listing Hooks

You can see all hooks in execution order, with their priority, source layer,
and status (disabled, runs on failure) using the CLI:

```bash
lemming hooks list
```

### Runner Selection

Hooks always use the same runner as your tasks. This ensures consistency and
simplifies configuration. Command-line arguments passed after `--` are
automatically forwarded to both the main task execution and any subsequent
hooks.
