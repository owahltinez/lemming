import unittest

from stats import summary


class TestSummary(unittest.TestCase):
    def test_total(self):
        self.assertEqual(summary.total([1, 2, 3]), 6)

    def test_mean(self):
        self.assertEqual(summary.mean([1, 2, 3]), 2)

    def test_mean_of_nothing(self):
        with self.assertRaises(ValueError):
            summary.mean([])
