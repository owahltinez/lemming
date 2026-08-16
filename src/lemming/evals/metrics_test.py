import pathlib
import shutil
import tempfile
import unittest
import unittest.mock

import readability

from lemming.evals import fixtures, metrics

_CLEAN = '''"""A clean module."""


def add(a: int, b: int) -> int:
    """Returns the sum of two numbers."""
    return a + b
'''

_DIRTY = '''"""A module carrying lint debt."""

import sys
import math


def add(a: int, b: int) -> int:
    return a + b
'''

_HIDDEN_TEST_PATH = "pkg/mod_hidden_test.py"

_HIDDEN_TEST = """import unittest

from pkg import mod


class TestAdd(unittest.TestCase):
    def test_add(self):
        assert mod.add(2, 3) == 5
"""


class MetricsTestCase(unittest.TestCase):
    def setUp(self):
        self.workspace = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

    def seed(self, files):
        fixtures.init_repo(self.workspace, files)

    def write(self, relative_path, content):
        target = self.workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


class TestUnresolvedFindings(MetricsTestCase):
    def test_clean_source_reports_nothing(self):
        self.seed({"pkg/mod.py": _CLEAN})

        self.assertEqual(
            metrics.unresolved_findings(self.workspace, ["pkg/mod.py"]), ""
        )

    def test_reports_the_rules_the_agent_was_told_to_run(self):
        # Dirty by readability's standard must come back as findings.
        self.seed({"pkg/mod.py": _DIRTY})

        outstanding = metrics.unresolved_findings(
            self.workspace, ["pkg/mod.py"]
        )

        self.assertIn("findings", outstanding)

    def test_test_files_follow_the_tools_own_per_file_ignores(self):
        # The grader inherits readability's per-file ignores for tests.
        self.seed({"pkg/mod_test.py": "def helper():\n    return 1\n"})

        self.assertEqual(
            metrics.unresolved_findings(self.workspace, ["pkg/mod_test.py"]),
            "",
        )

    def test_skips_paths_that_are_not_python_files(self):
        self.seed({"README.md": "# Title\n"})

        self.assertEqual(
            metrics.unresolved_findings(
                self.workspace, ["README.md", "pkg/gone.py"]
            ),
            "",
        )

    def test_being_unable_to_check_is_not_a_clean_result(self):
        # No findings from a tool that never ran is not a pass.
        self.seed({"pkg/mod.py": _CLEAN})
        unverified = {
            "no tool ran at all": readability.CheckReport(),
            "an applicable tool was missing": readability.CheckReport(
                ran={"ruff"}, skipped={"pyrefly"}
            ),
            "a tool could not finish": readability.CheckReport(
                ran={"ruff"}, failed={"pyrefly"}
            ),
        }

        for label, report in unverified.items():
            with self.subTest(label):
                with unittest.mock.patch.object(
                    metrics.readability, "check_paths", return_value=report
                ):
                    outstanding = metrics.unresolved_findings(
                        self.workspace, ["pkg/mod.py"]
                    )

                self.assertTrue(outstanding, f"{label} was read as clean")


class TestSourceFacts(unittest.TestCase):
    def test_top_level_functions_ignores_nested_definitions(self):
        source = (
            "def outer():\n"
            "    def inner():\n"
            "        return 1\n"
            "    return inner\n"
            "\n"
            "\n"
            "class Thing:\n"
            "    def method(self):\n"
            "        return 2\n"
        )

        self.assertEqual(metrics.top_level_functions(source), ["outer"])

    def test_unparseable_source_reports_nothing(self):
        self.assertEqual(metrics.top_level_functions("def broken(:\n"), [])
        self.assertEqual(
            metrics.called_names("def broken(:\n", "broken"), set()
        )

    def test_called_names_covers_plain_and_attribute_calls(self):
        source = (
            "def scale(x):\n"
            "    return helper(math.fabs(x))\n"
            "\n"
            "\n"
            "def helper(x):\n"
            "    return x\n"
        )

        self.assertEqual(
            metrics.called_names(source, "scale"), {"helper", "fabs"}
        )
        self.assertEqual(metrics.called_names(source, "helper"), set())

    def test_function_source_returns_the_definition_verbatim(self):
        # Graders match this text against an agent's file, byte for byte.
        source = (
            '"""Module."""\n'
            "\n"
            "\n"
            "def first(x):\n"
            '    """Doc."""\n'
            "    return x\n"
            "\n"
            "\n"
            "def second(x):\n"
            "    return x\n"
        )

        self.assertEqual(
            metrics.function_source(source, "first"),
            'def first(x):\n    """Doc."""\n    return x\n',
        )
        self.assertEqual(
            metrics.function_source(source, "second"),
            "def second(x):\n    return x\n",
        )

    def test_function_source_refuses_to_return_nothing(self):
        with self.assertRaises(ValueError):
            metrics.function_source("X = 1\n", "missing")


class TestHiddenTests(MetricsTestCase):
    def setUp(self):
        super().setUp()
        self.seed({"pkg/__init__.py": "", "pkg/mod.py": _CLEAN})
        self.hidden = {_HIDDEN_TEST_PATH: _HIDDEN_TEST}

    def test_preserved_behavior_passes_and_leaves_no_drift(self):
        result = metrics.run_hidden_tests(self.workspace, self.hidden)

        self.assertTrue(result.passed, result.detail)
        self.assertFalse((self.workspace / _HIDDEN_TEST_PATH).exists())
        self.assertEqual(fixtures.dirty_paths(self.workspace), [])

    def test_changed_behavior_fails_and_still_leaves_no_drift(self):
        self.write("pkg/mod.py", _CLEAN.replace("a + b", "a - b"))

        result = metrics.run_hidden_tests(self.workspace, self.hidden)

        self.assertFalse(result.passed)
        self.assertIn("test_add", result.detail)
        self.assertFalse((self.workspace / _HIDDEN_TEST_PATH).exists())

    def test_refuses_to_overwrite_a_workspace_file(self):
        # Clobbering agent code would corrupt every check in the pass.
        self.write(_HIDDEN_TEST_PATH, "# agent-authored\n")

        with self.assertRaises(FileExistsError):
            metrics.run_hidden_tests(self.workspace, self.hidden)


if __name__ == "__main__":
    unittest.main()
