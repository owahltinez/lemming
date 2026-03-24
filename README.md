# Lemming 🐹

**The transparent, tool-agnostic orchestrator for autonomous AI coding agents.**

Lemming bridges the gap between high-level project strategy and low-level agent execution. Instead of letting an agent wander through your codebase in a single, massive context window, Lemming forces a structured, iterative workflow via a human-readable `tasks.yml` file.

## Why Lemming?

*   **Zero Context Drift**: By breaking projects into discrete tasks, Lemming ensures agents stay focused. They only see the project context, relevant history, and the specific task at hand.
*   **Transparency & Control**: Every decision, technical finding, and outcome is recorded in a human-readable `tasks.yml` file. You can step in, adjust the roadmap, or swap agents at any time.
*   **Tool Agnostic**: Lemming doesn't care which agent you use. It works out-of-the-box with `gemini`, `aider`, `claude`, `codex`, or even your own custom scripts.
*   **Resilient Execution**: With built-in heartbeat monitoring, automatic retries, and outcome tracking, Lemming handles process crashes and rate limits gracefully.
*   **Human-Agent Collaboration**: Use the CLI or the Web UI to collaborate with your agents in real-time. Mark tasks, edit descriptions, and review outcomes as they happen.

---

## Installation

Install globally using `uv`:

```bash
uv tool install git+https://github.com/owahltinez/lemming.git
```

## Quick Start in 3 Steps

### 1. Scaffold the Roadmap
Initialize your project context and define your goals.

```bash
# Set project-wide rules (e.g. tech stack, style guides)
lemming context "Use React, TypeScript, and Tailwind. Follow TDD."

# Add tasks to the queue
lemming add "Initialize the project with Vite"
lemming add "Create the Button component"
lemming add "Implement the authentication flow"
```

### 2. Review and Refine
See exactly what's pending and what the agent will see.

```bash
# Show the current roadmap
lemming status
```

### 3. Release the Lemming
Start the autonomous loop.

```bash
# Run using the default agent (gemini)
lemming run

# Or use a different agent (flags after -- are passed to the agent)
lemming run --agent aider -- --model claude-3-5-sonnet
```

---

## The Web Dashboard

Lemming includes a modern, fast Web UI to monitor your projects.

```bash
lemming serve

# Or share it remotely via a secure tunnel with token auth
lemming serve --tunnel cloudflare
```

*   **Real-time Monitoring**: Watch tasks move from pending to in-progress to completed.
*   **Project Explorer**: A built-in, `.gitignore`-aware file browser to inspect your workspace alongside the roadmap.
*   **Interactive Controls**: Add tasks, edit context, and manage the execution loop from your browser.

---

## How it Works

Lemming maintains a human-readable `tasks.yml` file containing your project context, a queue of tasks, and recorded outcomes. When you run `lemming run`, it loops through each pending task:

1.  **Build a scoped prompt**: Lemming assembles a prompt containing only the project context, a summary of completed tasks and their outcomes, and the current task description.
2.  **Invoke the agent**: It launches your chosen agent CLI with that prompt, monitors it with heartbeats, and streams output to a log file.
3.  **Collect results**: The agent reports back via the Lemming CLI — recording findings with `lemming outcome`, then marking the task with `lemming complete` or `lemming fail`. Agents can also schedule new tasks with `lemming add`, breaking down complex work into smaller steps that Lemming will pick up automatically.
4.  **Retry or advance**: On failure, Lemming retries the task (up to `--max-attempts`) with accumulated outcomes as context, so the agent learns from previous attempts. On success, it moves to the next task.

---

## Command Reference

### Roadmap Management
*   **`status [<id>]`**: Roadmap overview or deep-dive into a specific task.
*   **`context [<text>]`**: Set or view project-wide instructions. Supports `-f/--file`.
*   **`add <desc>`**: Append a new task. Supports `--index` and `--agent`.
*   **`edit <id>`**: Modify a task's description, agent, or position.
*   **`delete <id>`**: Remove a task. Supports `--all` and `--completed` for bulk operations.
*   **`outcome <id> <finding>`**: Record a technical detail (e.g., "Database schema is in /migrations").

### Task Status
*   **`complete <id>`**: Mark a task as successful.
*   **`fail <id>`**: Report a blocker or failure for retry.
*   **`cancel <id>`**: Stop an in-progress task (kills the agent process).
*   **`reset <id>`**: Clear attempts and outcomes to start a task fresh.

### Execution
*   **`run`**: Start the orchestrator loop.
    *   `--max-attempts`: Retries per task (default 3).
    *   `--agent`: The CLI tool to invoke.
    *   `--env`: Set environment variables for the agent (e.g., `--env OPENAI_API_KEY=sk-...`). Can be used multiple times.
    *   `--`: Use `--` to pass any flag directly to the underlying agent.
*   **`serve`**: Launch the interactive Web UI.
    *   `--tunnel cloudflare|tailscale`: Expose the UI to the public internet via a secure tunnel.
    *   `--timeout`: Auto-shutdown after a duration (e.g., `8h`, `30m`). Defaults to `8h` with `--tunnel`, disabled otherwise.

---

## Advanced: Agent Customization

Lemming uses **fuzzy matching** to automatically inject the correct "YOLO" (auto-approve) and "Quiet" flags for popular tools:

*   **Gemini**: Adds `--yolo --no-sandbox`
*   **Aider**: Adds `--yes --quiet`
*   **Claude**: Adds `--dangerously-skip-permissions`
*   **Codex**: Adds `--yolo`

You can disable this behavior with `--no-defaults` or override the prompt flag with `--prompt-arg`.

---

## Screenshots

### Dashboard
![Dashboard](docs/screenshots/dashboard-desktop.png)

### Task Log
![Task Log](docs/screenshots/task-log-desktop.png)
