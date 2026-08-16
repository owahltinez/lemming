import unittest

from calc import limits


class TestLimits(unittest.TestCase):
    def test_percentage_is_clamped_to_the_display_range(self):
        self.assertEqual(limits.clamp_percentage(-4.0), 0.0)
        self.assertEqual(limits.clamp_percentage(140.0), 100.0)
        self.assertEqual(limits.clamp_percentage(42.5), 42.5)

    def test_retries_are_clamped_to_the_backoff_budget(self):
        self.assertEqual(limits.clamp_retries(-1), 0)
        self.assertEqual(limits.clamp_retries(9), 5)
        self.assertEqual(limits.clamp_retries(3), 3)
