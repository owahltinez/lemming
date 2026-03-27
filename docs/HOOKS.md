# Orchestrator Hooks ⚓️

Orchestrator hooks are a powerful way to extend the Lemming workflow. They allow you to execute custom agents or scripts after each task, enabling automated roadmap revision, code quality checks, or any other post-task validation.

## How it Works

When a task finishes (whether it succeeded or failed), Lemming can run one or more **orchestrator hooks**. Each hook:
1.  **Loads a prompt template**: Lemming looks for a `.md` file corresponding to the hook name.
2.  **Renders the prompt**: It injects context about the current roadmap and the task that just finished.
3.  **Invokes an agent**: It runs your configured runner with the rendered prompt.
4.  **Captures the output**: The agent's output is appended to the task's execution log, and any commands it runs (e.g. `lemming add`, `lemming edit`) are executed against the roadmap.

## Built-in Hooks

### `roadmap` (Default)
The `roadmap` hook is the primary mechanism for autonomous project management. It analyzes the results of the finished task and decides if the remaining roadmap needs to be adjusted (e.g., adding a missing prerequisite, skipping obsolete tasks, or breaking down a broad task).

## Custom Hooks

You can define your own hooks by creating Markdown files in the `.lemming/hooks/` directory of your project.

### Example: `lint` hook
Create `.lemming/hooks/lint.md`:

```markdown
You are a code quality orchestrator. A task has just finished.
Review the files changed in this task and ensure they follow the project's style guide.
If there are issues, add a new task to fix them.

### Roadmap Context
{{roadmap}}

### Finished Task
{{finished_task}}
```

## Using Hooks

### Configuration
By default, Lemming runs all hooks configured in your `tasks.yml`. If no hooks are explicitly configured, all available hooks (built-in and project-specific) are executed.

Use the `hooks` command group to manage which hooks should run automatically:

```bash
# Enable or disable one or more hooks
lemming hooks enable lint
lemming hooks disable roadmap lint

# Set the exact list of active hooks
lemming hooks set roadmap lint

# Reset to run all available hooks (default)
lemming hooks reset
```

Changes to hook configuration are picked up dynamically by the running orchestrator loop. You can toggle hooks from the CLI or the Web UI while Lemming is running, and the next task execution will respect the updated settings.

## Creating Custom Hooks

### Search Paths
Lemming looks for hook templates in the following order:
1.  `.lemming/hooks/{name}.md` (Project-specific hooks)
2.  Built-in hooks (e.g., `roadmap`)

### Available Variables
Your hook template can use the following placeholders:
-   `{{roadmap}}`: A structured summary of the entire project context and all tasks.
-   `{{finished_task}}`: Details about the task that just finished (ID, description, outcomes, and the last 100 lines of its execution log).
-   `{{tasks_file_name}}`: The filename of the tasks YAML file.
-   `{{tasks_file_path}}`: The full path to the tasks YAML file.

## Developer Ergonomics

### Listing Hooks
You can see all available hooks (built-in and local) using the CLI:

```bash
lemming hooks list
```

### Runner Selection
Hooks always use the same runner as your tasks. This ensures consistency and simplifies configuration. Command-line arguments passed after `--` are automatically forwarded to both the main task execution and any subsequent hooks.
