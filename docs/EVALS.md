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

### Pair Every Action Scenario With a Restraint One

A comparison of two agent CLIs found the arms failed in opposite directions:
one under-acted (it did not remove dead code, did not consolidate duplication)
and the other over-acted (it edited a healthy roadmap it was told to leave
alone). A suite made only of "did the agent act" scenarios therefore crowns
whichever agent edits most, and a suite made only of "did the agent hold back"
scenarios crowns whichever edits least — neither measures judgement.

So a new scenario needs a counterpart pulling the other way, and an agent
running a constant policy in either direction should score 50% across the pair.
`false-reuse-restraint` exists for exactly this reason: it is the inverse of
`consolidate-or-report-live-duplication`, with two functions that share a shape
but not a concept, and folding them together is the failure.

`lemming.evals.metrics` holds graders computed from tool output rather than
hand-written fixture knowledge: ruff findings under lemming's own rule set,
syntax-tree facts, and `run_hidden_tests`, which copies a test file in only at
grading time so the agent cannot rewrite the suite that judges it. Metrics that
score "did nothing" as perfect (churn, net lines) belong only next to a positive
requirement, and a metric that can be improved by making the code worse — radon
complexity rewards splitting one function into two — does not belong at all.
