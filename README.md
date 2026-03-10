# Lemming

An autonomous, iterative task runner for AI coding agents.

Lemming orchestrates AI coding agents by walking through a structured `tasks.yml` project file **sequentially**. It manages the global context, tracks task attempts, and records technical lessons learned directly in the YAML file, ensuring 100% state transparency and zero context drift.

It is tool-agnostic and works out-of-the-box with agentic CLIs like `gemini` (default), `aider`, `claude`, and `codex`.

## Installation

Install globally using `uv tool`:

```bash
uv tool install git+https://github.com/owahltinez/lemming.git
```

## Quick Start

Lemming relies on a single `tasks.yml` file in your project root. 

### 1. Scaffold your Project
Create a `tasks.yml` file, or use the CLI to generate it automatically:

```bash
# Set the overarching architectural rules
lemming set-context "Use Python 3.10 and the Click framework. Always write unit tests."

# Add tasks to the roadmap
lemming add "Implement the backend logic"
lemming add "Write unit tests"
```

### 2. Review the Roadmap
Check the current status and context of your project:
```bash
lemming info
lemming status
```

### 3. Run the Agent Loop
Trigger the autonomous orchestrator. Lemming will invoke the underlying agent, feed it the context and the current task, and wait for the agent to report back via the `lemming task` subcommands.

```bash
# Execute the autonomous agent loop
lemming run --max-attempts 3 --retry-delay 10
```

## Advanced Usage

### Tool Agnosticism & Arbitrary Flags
Lemming acts as a prompt-injector and orchestrator. It uses **fuzzy matching** on the agent's filename to automatically inject the correct YOLO/auto-approve flags (e.g. any binary starting with `gemini` like `gemini-v2` will get `--yolo --no-sandbox`).

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
When `lemming run` invokes an agent, it strictly instructs the agent **not** to edit the `tasks.yml` file manually. Instead, the agent is instructed to use Lemming's internal API:

*   **On Success:** `lemming task complete <task_id>`
*   **On Failure/Blocker:** `lemming task fail <task_id> --lesson "I got stuck because..."`

This guarantees that your project state (and the lessons learned by the agent) are recorded perfectly every time.
