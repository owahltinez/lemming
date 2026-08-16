import json
import pathlib
import unittest
import unittest.mock

import click.testing

from lemming.evals import cli, harness, scenarios


def _result(
    scenario: str,
    trial: int,
    passed: bool,
    checks: list[scenarios.Check] | None = None,
) -> harness.TrialResult:
    if checks is None:
        checks = [scenarios.Check(name="repaired", passed=passed, detail="x")]
    return harness.TrialResult(
        scenario=scenario,
        trial=trial,
        passed=passed,
        checks=checks,
        duration=1.0,
        workspace=pathlib.Path(f"/runs/{scenario}/trial-{trial}/workspace"),
    )


class TestInfraFailureReporting(unittest.TestCase):
    def invoke(self, results):
        with (
            unittest.mock.patch.object(
                cli.harness, "run_suite", return_value=results
            ),
            unittest.mock.patch.object(cli.container, "build_image"),
        ):
            return click.testing.CliRunner().invoke(
                cli.cli, ["run", "--skip-build", "--trials", "2"]
            )

    def test_infra_failures_are_called_out_separately(self):
        # Infra failures are no quality signal, so the report splits them.
        dead = _result("fast-exit-healthy", 0, False)
        dead.launch_failed = True
        results = [dead, _result("fast-exit-healthy", 1, True)]

        outcome = self.invoke(results)

        self.assertIn("1 infra failure", outcome.output)

    def test_clean_runs_do_not_mention_infra_failures(self):
        results = [_result("fast-exit-healthy", i, True) for i in range(2)]

        outcome = self.invoke(results)

        self.assertNotIn("infra failure", outcome.output)


class TestListCommand(unittest.TestCase):
    def test_lists_roadmap_scenarios(self):
        result = click.testing.CliRunner().invoke(cli.cli, ["list"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("roadmap", result.output)
        self.assertIn("repair-exhausted-failure", result.output)


class TestRunCommand(unittest.TestCase):
    def invoke(self, args, results):
        with (
            unittest.mock.patch.object(
                cli.harness, "run_suite", return_value=results
            ) as run_suite,
            unittest.mock.patch.object(cli.container, "build_image"),
        ):
            outcome = click.testing.CliRunner().invoke(
                cli.cli, ["run", "--skip-build", *args]
            )
        return outcome, run_suite

    def test_reports_pass_rates_and_exits_zero_on_success(self):
        results = [_result("fast-exit-healthy", i, True) for i in range(2)]

        outcome, _ = self.invoke(["--trials", "2"], results)

        self.assertEqual(outcome.exit_code, 0, outcome.output)
        self.assertIn("fast-exit-healthy: 2/2", outcome.output)

    def test_exits_nonzero_below_min_pass_rate(self):
        results = [
            _result("fast-exit-healthy", 0, True),
            _result("fast-exit-healthy", 1, False),
        ]

        outcome, _ = self.invoke(["--trials", "2"], results)

        self.assertEqual(outcome.exit_code, 1)
        self.assertIn("fast-exit-healthy: 1/2", outcome.output)
        self.assertIn("workspace", outcome.output)

    def test_advisory_reds_are_shown_but_do_not_gate(self):
        checks = [
            scenarios.Check(name="roadmap-extended", passed=True),
            scenarios.Check(
                name="gap-covered",
                passed=False,
                detail="new tasks: [...]",
                advisory=True,
            ),
        ]
        results = [_result("extend-goal-unmet", 0, True, checks)]

        outcome, _ = self.invoke(["--trials", "1"], results)

        self.assertEqual(outcome.exit_code, 0, outcome.output)
        self.assertIn("extend-goal-unmet: 1/1", outcome.output)
        self.assertIn("inspect: gap-covered", outcome.output)

    def test_min_pass_rate_threshold_is_configurable(self):
        results = [
            _result("fast-exit-healthy", 0, True),
            _result("fast-exit-healthy", 1, False),
        ]

        outcome, _ = self.invoke(
            ["--trials", "2", "--min-pass-rate", "0.5"], results
        )

        self.assertEqual(outcome.exit_code, 0, outcome.output)

    def test_scenario_filter_limits_suite(self):
        results = [_result("fast-exit-healthy", 0, True)]

        _, run_suite = self.invoke(["--scenario", "fast-exit-healthy"], results)

        suite = run_suite.call_args.args[0]
        self.assertEqual([s.name for s in suite], ["fast-exit-healthy"])

    def test_unknown_scenario_is_rejected(self):
        outcome, _ = self.invoke(["--scenario", "nope"], [])

        self.assertNotEqual(outcome.exit_code, 0)
        self.assertIn("Unknown scenarios", outcome.output)

    def test_json_report_is_written(self):
        runner = click.testing.CliRunner()
        results = [_result("fast-exit-healthy", 0, True)]
        with runner.isolated_filesystem():
            with (
                unittest.mock.patch.object(
                    cli.harness, "run_suite", return_value=results
                ),
                unittest.mock.patch.object(cli.container, "build_image"),
            ):
                outcome = runner.invoke(
                    cli.cli,
                    ["run", "--skip-build", "--json-report", "report.json"],
                )
            self.assertEqual(outcome.exit_code, 0, outcome.output)
            report = pathlib.Path("report.json").read_text()

        self.assertIn('"scenario": "fast-exit-healthy"', report)
        self.assertIn('"passed": true', report)

    def test_json_report_records_how_the_run_was_configured(self):
        # Two reports are indistinguishable without the configuration.
        runner = click.testing.CliRunner()
        results = [_result("fast-exit-healthy", 0, True)]
        with runner.isolated_filesystem():
            with (
                unittest.mock.patch.object(
                    cli.harness, "run_suite", return_value=results
                ),
                unittest.mock.patch.object(cli.container, "build_image"),
            ):
                outcome = runner.invoke(
                    cli.cli,
                    [
                        "run",
                        "--skip-build",
                        "--runner",
                        "opencode --variant high",
                        "--trials",
                        "2",
                        "--json-report",
                        "report.json",
                    ],
                )
            self.assertEqual(outcome.exit_code, 0, outcome.output)
            payload = json.loads(pathlib.Path("report.json").read_text())

        self.assertEqual(payload["config"]["runner"], "opencode --variant high")
        self.assertEqual(payload["config"]["trials"], 2)
        self.assertEqual(payload["config"]["suite"], "roadmap")
        self.assertIn("started_at", payload["config"])
        self.assertEqual(len(payload["results"]), 1)


class TestCompareCommand(unittest.TestCase):
    def write(self, directory, name, trials, runner):
        path = pathlib.Path(directory) / name
        path.write_text(
            json.dumps({"config": {"runner": runner}, "results": trials})
        )
        return str(path)

    def trial(self, scenario, passed, **extra):
        row = {
            "scenario": scenario,
            "trial": 0,
            "passed": passed,
            "checks": [],
            "duration": 60.0,
            "workspace": "/runs/x",
            "infra_failure": False,
        }
        row.update(extra)
        return row

    def invoke(self, left_trials, right_trials):
        runner = click.testing.CliRunner()
        with runner.isolated_filesystem() as directory:
            left = self.write(directory, "a.json", left_trials, "agy")
            right = self.write(directory, "b.json", right_trials, "opencode")
            return runner.invoke(cli.cli, ["compare", left, right])

    def test_shows_both_arms_per_scenario(self):
        outcome = self.invoke(
            [self.trial("one", True), self.trial("two", False)],
            [self.trial("one", False), self.trial("two", False)],
        )

        self.assertEqual(outcome.exit_code, 0, outcome.output)
        self.assertIn("agy", outcome.output)
        self.assertIn("opencode", outcome.output)
        self.assertIn("one", outcome.output)

    def test_marks_scenarios_that_cannot_separate_the_arms(self):
        outcome = self.invoke(
            [self.trial("same", True)], [self.trial("same", True)]
        )

        self.assertIn("degenerate", outcome.output)

    def test_says_when_the_run_cannot_separate_the_arms(self):
        # A percentage gap in a small run must not read as a finding.
        outcome = self.invoke(
            [self.trial("one", True), self.trial("one", False)],
            [self.trial("one", False), self.trial("one", False)],
        )

        self.assertIn("No separation", outcome.output)

    def test_reports_a_real_difference_rather_than_hiding_it(self):
        # The intervals overlap here, but the arms genuinely differ.
        outcome = self.invoke(
            [self.trial("one", True)] * 12,
            [self.trial("one", True)] * 7 + [self.trial("one", False)] * 5,
        )

        self.assertIn("Arms differ", outcome.output)

    def test_counts_infra_failures_separately(self):
        outcome = self.invoke(
            [self.trial("one", False, infra_failure=True)],
            [self.trial("one", True)],
        )

        self.assertIn("infra", outcome.output)


if __name__ == "__main__":
    unittest.main()
