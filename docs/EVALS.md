# Prompt Evals 🧪

Lemming's behavior is driven by prompts (the task runner and the orchestrator
hooks), so a prompt edit can regress the system without any test failing. The
eval harness in `lemming.evals` catches that: it replays realistic "a task just
finished" situations against the real hook execution path and grades the outcome
with mechanical checks — no LLM judging involved.

## How it Works

Each **scenario** seeds a hermetic fixture: a tiny git repository plus a
`tasks.yml` mid-flight (e.g. a task that just failed for the third time). A
**trial** runs the hook under eval against that fixture using the same code path
as the orchestrator, then a **grader** inspects the aftermath:

- Did the roadmap hook repair a task that failed at max attempts, or did it
  naively reset it?
- Did it leave a healthy roadmap untouched?
- Did it keep its hands off source files (`git status` must stay clean)?

Every trial runs in its own container built from the repo `Dockerfile`, with
only the fixture workspace and a per-trial `LEMMING_HOME` mounted. The agent
under eval cannot touch the host, and concurrent trials share no state, so
trials run in parallel safely.

### Hook and Task Scenarios

A scenario declares a `mode`. The default, `hook`, is the flow above: an
orchestrator hook reacts to a task that just finished. A `task` scenario instead
sets a `prompt` and no hook fields; the trial runs that prompt as a one-shot
through `lemming exec` against the fixture workspace, and the grader judges the
code the agent actually wrote. Everything else — isolation, runner config, infra
failure classification — is identical in both modes.

## Running

Evals invoke real agents: expect minutes of wall clock and real token spend.
They are a manual gate for prompt changes, not part of the unit test suite.

```bash
# List available suites and scenarios
uv run python -m lemming.evals list

# Run a suite (roadmap or readability): scenarios x 3 trials in parallel
uv run python -m lemming.evals run --suite roadmap
uv run python -m lemming.evals run --suite readability

# Iterate on a single scenario with fewer trials
uv run python -m lemming.evals run \
    --scenario repair-exhausted-failure --trials 1 --skip-build

# Machine-readable results and a non-default pass threshold
uv run python -m lemming.evals run --json-report report.json --min-pass-rate 0.67
```

The command prints per-scenario pass rates (agents are stochastic, so think in
rates, not booleans) and, for each failed trial, the failing checks plus the
kept workspace path so you can inspect exactly what the agent did. Runner logs
land in the trial's `home/` directory next to the workspace. The exit code is
non-zero when any scenario's pass rate drops below `--min-pass-rate`.

### Required vs Advisory Checks

Most checks encode the prompt's hard contract mechanically (no source changes,
no naive task resets, tests stay green) — a red there is a defect, and the right
response is to improve the prompt. A few checks grade a semantic property
through a keyword proxy (e.g. "the added task mentions multiply") and are marked
**advisory**: they never fail a trial and print as yellow `inspect:` lines
instead. An advisory red means read the workspace and adjudicate — if the
agent's output was genuinely fine, widen the proxy; if it was vague, tighten the
prompt. Keeping proxies out of the pass/fail gate is what keeps hard reds
trustworthy.

### Credentials

The default runner is `agy`. Each trial automatically receives a private,
disposable copy of the runner's host config: `~/.gemini` for `agy` and
`~/.config/opencode` for `opencode`. Containers can therefore refresh tokens
and write state without ever touching the real config, and concurrent trials
stay fully isolated from each other.

Caches, history, and conversations are excluded, as are the model cache and
vendored CLI under `antigravity-cli/` and any `node_modules` — the container
installs its own agents, and those directories are hundreds of megabytes that
would otherwise be copied once per trial. Auth state is always kept.

What each copy carries is also what makes a comparison between two runners
meaningful, and the rule is parity: every arm gets its global instructions and
its credentials, and anything one runner has no counterpart for is left behind.
agy's `skills/` and `extensions/` are excluded for exactly that reason. An arm
running with extra tooling is a differently equipped agent, and a result that
turns on the difference measures the equipment rather than the runner.

Global instructions are the one piece of context both arms are expected to
share, so point them at the same file — for example symlinking `~/AGENTS.md`
to both `~/.gemini/GEMINI.md` and `~/.config/opencode/AGENTS.md`. A trial copy
dereferences the symlink, so the container sees the real contents.

For API-key runners, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`,
`GEMINI_API_KEY`, and `GOOGLE_API_KEY` are forwarded into the container when set
on the host. Any other credential files can be mounted with `--volume`.

### Interpreting failures

A trial fails when any non-advisory check is red, but not every red says
something about the agent. Trials whose runner never started (missing binary,
bad credentials) or ran out of time are reported separately as **infra
failures**, and carry `launch_failed` / `timed_out` / `exit_codes` in the JSON
report. They still count as failures — a dead runner leaves a pristine workspace
that can otherwise look like a well-behaved fast exit — but a pass rate dragged
down by them is not a quality signal. Comparing two runners with different infra
failure rates compares infrastructure, not judgement.

## Adding Scenarios

Scenarios live in `src/lemming/evals/` (see `roadmap.py`):

1. Write a `build` function that seeds the workspace via `fixtures.init_repo`
   and `fixtures.save_roadmap`.
2. Write a `grade` function returning `scenarios.Check` results. Prefer checks
   that are mechanically verifiable: roadmap diffs, `fixtures.dirty_paths` for
   source drift, task statuses.
3. Register the scenario in the module's `SCENARIOS` list, and new suites in
   `suites.all_suites`.
4. Add unit tests that grade simulated good and bad agent behavior; the graders
   themselves must stay fast and offline.
