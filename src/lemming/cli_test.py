import unittest
import click.testing
from lemming import cli


class TestCLIProxy(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()

    def test_cli_proxy_help(self):
        # Verify that the proxy re-export works correctly
        result = self.cli_runner.invoke(cli.cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Lemming: An autonomous, iterative task runner", result.output)


if __name__ == "__main__":
    unittest.main()
