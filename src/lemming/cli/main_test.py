import unittest
import click.testing
from lemming.cli.main import cli


class TestCLIMain(unittest.TestCase):
    def setUp(self):
        self.cli_runner = click.testing.CliRunner()

    def test_cli_help(self):
        result = self.cli_runner.invoke(cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Lemming: An autonomous, iterative task runner", result.output)


if __name__ == "__main__":
    unittest.main()
