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
disposable copy of the host's `~/.gemini` config (caches, history, and the
bundled CLI excluded) mounted at `/root/.gemini`, so containers can refresh
tokens and write state without ever touching the real agy home — and concurrent
trials stay fully isolated from each other.

For API-key runners, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`,
`GEMINI_API_KEY`, and `GOOGLE_API_KEY` are forwarded into the container when set
on the host. Any other credential files can be mounted with `--volume`.

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
