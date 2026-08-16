import unittest

from calc import ops


class TestOps(unittest.TestCase):
    def test_add(self):
        self.assertEqual(ops.add(2, 3), 5)

    def test_subtract(self):
        self.assertEqual(ops.subtract(5, 3), 2)

    def test_add_for_receipt(self):
        self.assertEqual(ops.add_for_receipt(2.126, 1), 3.13)

    def test_subtract_for_receipt(self):
        self.assertEqual(ops.subtract_for_receipt(5.126, 2), 3.13)

    def test_receipt_totals_must_be_finite(self):
        with self.assertRaises(ValueError):
            ops.add_for_receipt(float("inf"), 1)
        with self.assertRaises(ValueError):
            ops.subtract_for_receipt(float("inf"), 1)
