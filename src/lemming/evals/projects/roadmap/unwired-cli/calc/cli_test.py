import unittest

from calc import cli


class TestCli(unittest.TestCase):
    def test_registered_commands(self):
        cases = {
            "add": (2, 3, 5),
            "subtract": (5, 3, 2),
        }
        for command, (left, right, expected) in cases.items():
            with self.subTest(command=command):
                self.assertEqual(cli.execute(command, left, right), expected)
