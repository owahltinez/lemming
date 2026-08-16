import unittest

from stats import summary


class TestSummaryContract(unittest.TestCase):
    def test_spread_is_the_largest_minus_the_smallest(self):
        self.assertEqual(summary.spread([4, 1, 3]), 3)
        self.assertEqual(summary.spread([-2.5, 2.5]), 5.0)

    def test_spread_of_a_single_value_is_zero(self):
        self.assertEqual(summary.spread([7]), 0)

    def test_spread_of_nothing_is_an_error(self):
        with self.assertRaises(ValueError):
            summary.spread([])

    def test_existing_summaries_still_hold(self):
        self.assertEqual(summary.total([1, 2, 3]), 6)
        self.assertEqual(summary.mean([1, 2, 3]), 2)
        with self.assertRaises(ValueError):
            summary.mean([])
