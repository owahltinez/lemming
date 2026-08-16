import pathlib
import shutil
import tempfile
import unittest

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


class TestRuffFindingCodes(MetricsTestCase):
    def test_clean_source_has_no_findings(self):
        self.seed({"pkg/mod.py": _CLEAN})

        codes = metrics.ruff_finding_codes(self.workspace, ["pkg/mod.py"])

        self.assertEqual(codes, [])

    def test_reports_the_rules_lemming_configures(self):
        # Pinning the codes proves the grader uses lemming's own rule set
        # rather than ruff's much smaller default selection.
        self.seed({"pkg/mod.py": _DIRTY})

        codes = metrics.ruff_finding_codes(self.workspace, ["pkg/mod.py"])

        self.assertEqual(sorted(codes), ["D103", "F401", "F401", "I001"])

    def test_test_files_are_exempt_from_docstring_rules(self):
        # The grader must apply the same per-file ignores the agent sees, or
        # it would demand docstrings the tool never asked for.
        self.seed({"pkg/mod_test.py": "def helper():\n    return 1\n"})

        codes = metrics.ruff_finding_codes(self.workspace, ["pkg/mod_test.py"])

        self.assertEqual(codes, [])

    def test_skips_paths_that_are_not_python_files(self):
        self.seed({"README.md": "# Title\n"})

        codes = metrics.ruff_finding_codes(
            self.workspace, ["README.md", "pkg/gone.py"]
        )

        self.assertEqual(codes, [])


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
