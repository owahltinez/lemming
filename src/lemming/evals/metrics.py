"""Objective measurements eval graders can compute from a workspace.

Everything here is mechanical: a lint tool's own JSON, the syntax tree, or
the exit code of a test run. Nothing infers intent, so a grader built from
these stays cheap to trust.
"""

import ast
import dataclasses
import json
import pathlib
import subprocess
import sys
import tempfile

# Mirrors the default configuration `readability check` applies when a
# project defines none, which is what an eval fixture gets. Grading against
# a different rule set than the agent was told to run would measure the
# disagreement between the two configs instead of the agent.
RUFF_CONFIG = """line-length = 80

[lint]
select = ["E", "W", "F", "I", "N", "D", "PL"]
ignore = ["PLR0911", "PLR0912", "PLR0913", "PLR0915", "PLR2004"]

[lint.pydocstyle]
convention = "google"

[lint.per-file-ignores]
"test_*.py" = ["D"]
"*_test.py" = ["D"]
"""


def ruff_finding_codes(workspace: pathlib.Path, paths: list[str]) -> list[str]:
    """Returns the ruff rule codes reported for the given workspace paths.

    ruff is invoked directly, in JSON, rather than through
    `readability check`: that command concatenates several tools' prose and
    has no stable shape to parse. Paths that are missing or are not Python
    files are skipped, so a caller can pass a raw list of changed files.

    Args:
        workspace: Root of the workspace repository.
        paths: Repo-relative paths to lint.

    Returns:
        One rule code per finding, in ruff's reporting order.
    """
    targets = [
        str(workspace / path)
        for path in paths
        if path.endswith(".py") and (workspace / path).is_file()
    ]
    if not targets:
        return []

    # The config travels as a file so the fixture needs none of its own and
    # ruff never walks up into whatever project encloses the temp workspace.
    with tempfile.TemporaryDirectory() as config_dir:
        config = pathlib.Path(config_dir) / "ruff.toml"
        config.write_text(RUFF_CONFIG)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--config",
                str(config),
                "--output-format",
                "json",
                "--no-cache",
                *targets,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    return [finding["code"] for finding in json.loads(result.stdout)]


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
