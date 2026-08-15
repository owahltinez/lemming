"""Objective measurements eval graders can compute from a workspace.

Everything here is mechanical: the lint tool's own verdict, the syntax
tree, or the exit code of a test run. Nothing infers intent, and nothing
restates a rule another tool already owns, so a grader built from these
stays cheap to trust.

Two metrics were deliberately left out, because a grader nobody trusts is
worse than no grader:

- **Cyclomatic complexity** (radon). Measured against the suite's own messy
  fixture, flattening the nesting by hand *raised* the score from 6 to 7
  (radon counts ``and``/``or``), while splitting the function in two
  dropped the named function to 2 without removing a single branch. The
  metric therefore ranks fragmentation above the honest fix, which is the
  pathology a readability eval exists to catch.
- **Diff churn / net lines.** Doing nothing scores a perfect zero, so it
  can only ever be read alongside a positive requirement, and the existing
  ``git status`` scope check already answers the threshold-free question
  ("was anything outside scope touched?") without inventing a line budget.
"""

import ast
import dataclasses
import pathlib
import subprocess
import sys


def unresolved_findings(workspace: pathlib.Path, paths: list[str]) -> str:
    """Returns what `readability check` still reports, empty when clean.

    The check is delegated rather than reimplemented. readability owns the
    rule set, decides which tools apply to a path, and distinguishes a
    clean result from one where the tools never ran; duplicating any of
    that here would grade the agent against a different standard than the
    one its prompt told it to meet, and would drift the moment either side
    changed. It is also the exact command the hook is told to run.

    Args:
        workspace: Root of the workspace repository.
        paths: Repo-relative paths to check.

    Returns:
        The tool's report of what is outstanding, or an empty string when
        it verified the paths and found nothing. Being unable to verify is
        not a clean result and returns a reason.
    """
    targets = [
        path
        for path in paths
        if path.endswith(".py") and (workspace / path).is_file()
    ]
    if not targets:
        return ""

    try:
        result = subprocess.run(
            ["readability", "check", *targets],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except OSError as exc:
        return f"could not run readability check: {exc}"

    # readability announces a verified-clean run explicitly, precisely so a
    # caller cannot read "no output" as approval. Anything else -- findings,
    # a tool that could not start, no tool applying at all -- leaves the
    # paths unverified, which is not the same as clean.
    if result.returncode == 0 and "No findings in" in result.stderr:
        return ""
    return (result.stdout + result.stderr).strip()[-400:]


def _parse(source: str) -> ast.Module | None:
    """Parses source, returning None when the agent left it unparseable."""
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def top_level_functions(source: str) -> list[str]:
    """Returns the names of the module-level functions defined in source.

    Nested and class-level definitions are excluded, so the result answers
    exactly one question: which functions does this module offer? An
    unchanged list means no helper was factored out.
    """
    tree = _parse(source)
    if tree is None:
        return []
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def called_names(source: str, function: str) -> set[str]:
    """Returns the names of everything one function calls.

    Attribute calls contribute their final attribute (``math.fabs`` gives
    ``fabs``), which is enough to tell whether two functions became coupled.

    Args:
        source: Module source to inspect.
        function: Name of the module-level function to look inside.

    Returns:
        Called names, or an empty set when the function is absent.
    """
    tree = _parse(source)
    if tree is None:
        return set()

    target = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == function
        ),
        None,
    )
    if target is None:
        return set()

    names = set()
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


@dataclasses.dataclass(frozen=True)
class HiddenTests:
    """Outcome of a hidden test run.

    Attributes:
        passed: Whether every hidden test passed.
        detail: Tail of the runner output, for a failure report.
    """

    passed: bool
    detail: str


def run_hidden_tests(
    workspace: pathlib.Path,
    tests: dict[str, str],
    timeout: int = 120,
) -> HiddenTests:
    """Runs tests that were kept out of the workspace until grading time.

    An agent can rewrite any test file it can see, so a visible suite
    passing only proves the code and its tests agree with each other. These
    are copied in after the trial, run, and removed again, which keeps the
    behavioural contract fixed at what the fixture author wrote.

    Args:
        workspace: Root of the workspace repository.
        tests: Repo-relative paths mapped to test module contents.
        timeout: Seconds to allow the test run.

    Returns:
        Whether the hidden tests passed, plus output for a failure report.

    Raises:
        FileExistsError: If a path is already present in the workspace.
            Clobbering agent-authored code would corrupt the other checks
            grading the same workspace.
    """
    written = []
    try:
        for relative_path, content in tests.items():
            target = workspace / relative_path
            if target.exists():
                raise FileExistsError(f"hidden test would overwrite {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            written.append(target)

        modules = [path[: -len(".py")].replace("/", ".") for path in tests]
        result = subprocess.run(
            [sys.executable, "-m", "unittest", *modules],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        detail = "" if result.returncode == 0 else result.stderr[-500:]
        return HiddenTests(passed=result.returncode == 0, detail=detail)
    finally:
        for target in written:
            target.unlink(missing_ok=True)
