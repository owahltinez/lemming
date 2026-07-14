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


if __name__ == "__main__":
    unittest.main()
