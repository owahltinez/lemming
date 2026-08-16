import json
import pathlib
import shutil
import tempfile
import unittest

from lemming.evals import report


def _trial(scenario: str, passed: bool, **extra) -> dict:
    trial = {
        "scenario": scenario,
        "trial": 0,
        "passed": passed,
        "checks": [],
        "duration": 60.0,
        "workspace": "/runs/x",
        "error": "",
        "exit_codes": {"readability": 0},
        "launch_failed": False,
        "timed_out": False,
        "infra_failure": False,
    }
    trial.update(extra)
    return trial


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def write(self, name: str, payload) -> pathlib.Path:
        path = self.directory / name
        path.write_text(json.dumps(payload))
        return path


class TestLoad(ReportTestCase):
    def test_reads_the_config_block(self):
        path = self.write(
            "a.json",
            {"config": {"runner": "agy"}, "results": [_trial("s", True)]},
        )

        loaded = report.load(path)

        self.assertEqual(loaded.config["runner"], "agy")
        self.assertEqual(len(loaded.results), 1)

    def test_reads_a_report_written_before_config_blocks(self):
        # Reports from an earlier run are a bare list of trials.
        path = self.write("old.json", [_trial("s", True)])

        loaded = report.load(path)

        self.assertEqual(loaded.config, {})
        self.assertEqual(len(loaded.results), 1)

    def test_label_falls_back_to_the_file_name(self):
        path = self.write("agy-roadmap.json", [_trial("s", True)])

        self.assertEqual(report.load(path).label, "agy-roadmap")

    def test_label_prefers_the_recorded_runner(self):
        path = self.write(
            "a.json", {"config": {"runner": "agy --model x"}, "results": []}
        )

        self.assertEqual(report.load(path).label, "agy --model x")


class TestWilsonInterval(unittest.TestCase):
    def test_brackets_the_observed_rate(self):
        low, high = report.wilson_interval(7, 10)

        self.assertLess(low, 0.7)
        self.assertGreater(high, 0.7)

    def test_more_trials_narrow_the_interval(self):
        narrow = report.wilson_interval(70, 100)
        wide = report.wilson_interval(7, 10)

        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_stays_within_zero_and_one_at_the_extremes(self):
        low, high = report.wilson_interval(0, 5)
        self.assertGreaterEqual(low, 0.0)

        low, high = report.wilson_interval(5, 5)
        self.assertLessEqual(high, 1.0)

    def test_no_trials_is_not_a_division_error(self):
        self.assertEqual(report.wilson_interval(0, 0), (0.0, 0.0))


class TestFisherExact(unittest.TestCase):
    def test_a_clear_difference_is_significant(self):
        self.assertLess(report.fisher_exact(12, 0, 7, 5), 0.05)

    def test_identical_arms_are_not(self):
        self.assertGreater(report.fisher_exact(5, 5, 5, 5), 0.9)

    def test_detects_a_difference_that_overlapping_intervals_hide(self):
        # Overlapping intervals are no evidence that the arms are alike.
        left = report.wilson_interval(27, 30)
        right = report.wilson_interval(19, 30)
        self.assertLessEqual(right[0], left[1])

        self.assertLess(report.fisher_exact(27, 3, 19, 11), 0.05)

    def test_an_empty_arm_is_not_a_division_error(self):
        self.assertEqual(report.fisher_exact(0, 0, 0, 0), 1.0)


class TestSummarize(ReportTestCase):
    def test_counts_per_scenario_and_pooled(self):
        loaded = report.load(
            self.write(
                "a.json",
                [
                    _trial("one", True),
                    _trial("one", False),
                    _trial("two", True),
                ],
            )
        )

        summary = report.summarize(loaded)

        self.assertEqual(summary.by_scenario["one"], (1, 2))
        self.assertEqual(summary.by_scenario["two"], (1, 1))
        self.assertEqual((summary.passed, summary.total), (2, 3))

    def test_separates_infra_failures_from_behaviour(self):
        loaded = report.load(
            self.write(
                "a.json",
                [
                    _trial("one", False, infra_failure=True, timed_out=True),
                    _trial("one", False),
                ],
            )
        )

        summary = report.summarize(loaded)

        self.assertEqual(summary.infra_failures, 1)

    def test_an_infra_failure_is_left_out_of_the_counts(self):
        # The agent never decided anything, so the trial is not evidence
        # either way; counting it as a loss penalizes the flakier arm.
        loaded = report.load(
            self.write(
                "a.json",
                [
                    _trial("one", False, infra_failure=True, timed_out=True),
                    _trial("one", True),
                ],
            )
        )

        summary = report.summarize(loaded)

        self.assertEqual(summary.by_scenario["one"], (1, 1))
        self.assertEqual((summary.passed, summary.total), (1, 1))
        self.assertEqual(summary.infra_failures, 1)

    def test_reports_median_duration(self):
        loaded = report.load(
            self.write(
                "a.json",
                [
                    _trial("one", True, duration=10.0),
                    _trial("one", True, duration=20.0),
                    _trial("one", True, duration=90.0),
                ],
            )
        )

        self.assertEqual(report.summarize(loaded).median_duration, 20.0)


class TestDegenerateScenarios(ReportTestCase):
    def summaries(self, left: list[dict], right: list[dict]):
        return (
            report.summarize(report.load(self.write("a.json", left))),
            report.summarize(report.load(self.write("b.json", right))),
        )

    def test_flags_scenarios_where_both_arms_agree_completely(self):
        # A scenario both arms always agree on cannot separate them.
        left, right = self.summaries(
            [_trial("always", True), _trial("never", False)],
            [_trial("always", True), _trial("never", False)],
        )

        self.assertEqual(
            report.degenerate_scenarios(left, right), ["always", "never"]
        )

    def test_a_scenario_that_separates_the_arms_is_not_degenerate(self):
        left, right = self.summaries(
            [_trial("split", True)], [_trial("split", False)]
        )

        self.assertEqual(report.degenerate_scenarios(left, right), [])


if __name__ == "__main__":
    unittest.main()
