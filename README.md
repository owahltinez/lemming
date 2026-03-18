# Lemming 🐹

**The transparent, tool-agnostic orchestrator for autonomous AI coding agents.**

Lemming bridges the gap between high-level project strategy and low-level agent execution. Instead of letting an agent wander through your codebase in a single, massive context window, Lemming forces a structured, iterative workflow via a shared **Project Roadmap**.

## Why Lemming?

*   **Zero Context Drift**: By breaking projects into discrete tasks, Lemming ensures agents stay focused. They only see the project context, relevant history, and the specific task at hand.
*   **Transparency & Control**: Every decision, technical finding, and outcome is recorded in a human-readable `tasks.yml` file. You can step in, adjust the roadmap, or swap agents at any time.
*   **Tool Agnostic**: Lemming doesn't care which agent you use. It works out-of-the-box with `gemini-code-assistant`, `aider`, `claude-engineer`, `codex`, or even your own custom scripts.
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
Start the autonomous loop. Lemming will pick the first pending task, invoke your preferred agent, and feed it the necessary context.

```bash
# Run using the default agent (gemini)
lemming run

# Or use a different agent with custom flags
lemming run --agent aider -- --model claude-3-5-sonnet
```

---

## The Web Dashboard

Lemming includes a modern, fast Web UI to monitor your projects.

```bash
lemming serve
```

*   **Real-time Monitoring**: Watch tasks move from pending to in-progress to completed.
*   **Project Explorer**: A built-in, `.gitignore`-aware file browser to inspect your workspace alongside the roadmap.
*   **Interactive Controls**: Add tasks, edit context, and manage the execution loop from your browser.

---

## How it Works: The Roadmap Architecture

Lemming operates on a **Strategic vs. Tactical** split:

1.  **Strategic (Lemming)**: Manages the `tasks.yml` file. It decides *what* needs to be done next and provides the agent with a "lesson learned" summary of previous attempts.
2.  **Tactical (Agent)**: Executes the specific task. It is strictly forbidden from editing the roadmap directly. Instead, it reports back via the Lemming API.

### The Agent Protocol
When an agent runs under Lemming, it is instructed to use these commands:
*   `lemming outcome <id> "finding"`: Record a technical detail (e.g. "Database schema is in /migrations").
*   `lemming complete <id>`: Mark the task as successful.
*   `lemming fail <id>`: Report a blocker or failure for retry.
*   `lemming add <desc> [--index N]`: Add or insert new tasks into the queue.
*   `lemming --help`: Explore the full list of available commands.

### Environment Overrides
You can pass custom environment variables to your agents, which is particularly useful for API keys or configuration that shouldn't be hardcoded.

*   **CLI**: Use the `--env` flag: `lemming run --env OPENAI_API_KEY=sk-...`
*   **Web UI**: Use the "Environment Overrides" section in the metadata card.

---

## Command Reference

### Roadmap Management
*   **`status [<id>]`**: Roadmap overview or deep-dive into a specific task.
*   **`context [<text>]`**: Set or view project-wide instructions. Supports `-f/--file`.
*   **`add <desc>`**: Append a new task. Supports `--index` and `--agent`.
*   **`edit <id>`**: Modify a task's description, agent, or position.
*   **`delete <id>`**: Remove a task. Supports `--all` and `--completed` for bulk operations.
*   **`cancel <id>`**: Stop an in-progress task (kills the agent process).
*   **`reset <id>`**: Clear attempts and outcomes to start a task fresh.

### Execution
*   **`run`**: Start the orchestrator loop.
    *   `--max-attempts`: Retries per task (default 3).
    *   `--agent`: The CLI tool to invoke.
    *   `--env`: Set environment variables for the agent (can be used multiple times).
    *   `--`: Use `--` to pass any flag directly to the underlying agent.
*   **`serve`**: Launch the interactive Web UI.

---

## Advanced: Agent Customization

Lemming uses **fuzzy matching** to automatically inject the correct "YOLO" (auto-approve) and "Quiet" flags for popular tools:

*   **Gemini**: Adds `--yolo --no-sandbox`
*   **Aider**: Adds `--yes --quiet`
*   **Claude**: Adds `--dangerously-skip-permissions`
*   **Codex**: Adds `--yolo`

You can disable this behavior with `--no-defaults` or override the prompt flag with `--prompt-flag`.
