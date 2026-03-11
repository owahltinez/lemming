# Lemming

An autonomous, iterative task runner for AI coding agents.

Lemming orchestrates AI coding agents by walking through a structured `tasks.yml` project roadmap **sequentially**. It manages the project context, tracks task attempts, and records technical lessons learned directly in the roadmap file, ensuring transparency and zero context drift.

It is tool-agnostic and works out-of-the-box with agentic CLIs like `gemini` (default), `aider`, `claude`, and `codex`.

## Installation

Install globally using `uv tool`:

```bash
uv tool install git+https://github.com/owahltinez/lemming.git
```

## Quick Start

Lemming relies on a single `tasks.yml` file. By default, it looks for `tasks.yml` in the current directory. If not found, it uses `~/.local/lemming/tasks.yml`.

### 1. Scaffold your Project
Set the overarching project context and add tasks to the queue:

```bash
# Set context from a string
lemming context "Use Python 3.10 and the Click framework. Always write unit tests."

# Or set context from a file
lemming context --file GUIDELINES.md

# Add tasks to the queue
lemming add "Implement the backend logic"
lemming add "Write unit tests"
```

### 2. Review the Roadmap
Check the current status and context of your project:
```bash
# Show pending tasks and context summary
lemming status

# Show all tasks (including completed ones) and full context
lemming status --verbose

# Show details for a specific task
lemming status <task_id>
```

### 3. Run the Autonomous Loop
Trigger the autonomous orchestrator. Lemming will invoke the underlying agent, feed it the context and the current task, and wait for the agent to report back.

```bash
# Execute the autonomous execution loop
lemming run --max-attempts 3 --retry-delay 10
```

## Commands Reference

### Project Management
*   **`context [<text>]`**: View or set the project context. Use `-f/--file` to read from a file.
*   **`add <description>`**: Add a new task. Use `--index <n>` to insert at a specific position, or `--agent <name>` to specify a custom agent for just this task.
*   **`edit <task_id>`**: Edit an existing task. Supports `--description`, `--agent`, and `--index`.
*   **`delete <task_id>`**: Remove a task from the queue.
*   **`reset <task_id>`**: Clear a task's attempts and lessons.
*   **`clear`**: Clear the task queue (default). Use `--context` to clear only context, or `--all` for both.
*   **`status [<task_id>]`**: Show roadmap overview or specific task details.
*   **`serve`**: Launch the web interface (defaults to http://127.0.0.1:8000).

### Task Status (used by agents or humans)
*   **`complete <task_id> --outcome <text>`**: Mark a task as completed with a summary of the work.
*   **`fail <task_id> --lesson <text>`**: Record a failure and a technical lesson for the next attempt.
*   **`uncomplete <task_id>`**: Mark a completed task as pending again.

### Global Options
*   **`--tasks-file <path>`**: Explicitly set the path to the `tasks.yml` file.
*   **`--verbose`, `-v`**: Enable detailed output for any command.

## Advanced Usage

### Tool Agnosticism & Arbitrary Flags
Lemming acts as a prompt-injector and orchestrator. It uses **fuzzy matching** on the agent's filename to automatically inject the correct YOLO/auto-approve flags (e.g. any binary starting with `gemini` will get `--yolo --no-sandbox`).

It can also pass arbitrary, unparsed arguments straight through to the underlying agent using the standard POSIX `--` separator.

```bash
# Lemming sees 'aider' and automatically adds '--yes'
# The custom model flag is passed straight through
lemming run --agent aider -- --model claude-3-5-sonnet

# Use a custom script named `gemini-wrapper`. 
# Lemming will fuzzy-match 'gemini' and add '--yolo'
lemming run --agent /path/to/gemini-wrapper

# Completely override auto-injection with --no-defaults
# Provide your own prompt flag and exact arguments
lemming run --agent my-custom-agent --no-defaults --prompt-flag message -- --verbose --debug
```

### How Agents Interact with Lemming
When `lemming run` invokes an agent, it strictly instructs the agent **not** to edit the `tasks.yml` file manually. Instead, the agent is instructed to use Lemming's internal API (`complete` or `fail`).

This guarantees that your project state, outcomes, and lessons learned are recorded perfectly every time.
