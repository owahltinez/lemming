import unittest

from lemming.evals import scenarios


class TestPassed(unittest.TestCase):
    def test_all_checks_passing(self):
        checks = [scenarios.Check(name="a", passed=True)]
        self.assertTrue(scenarios.passed(checks))

    def test_required_failure_fails_the_trial(self):
        checks = [
            scenarios.Check(name="a", passed=True),
            scenarios.Check(name="b", passed=False),
        ]
        self.assertFalse(scenarios.passed(checks))

    def test_advisory_failure_does_not_fail_the_trial(self):
        checks = [
            scenarios.Check(name="a", passed=True),
            scenarios.Check(name="proxy", passed=False, advisory=True),
        ]
        self.assertTrue(scenarios.passed(checks))


if __name__ == "__main__":
    unittest.main()
