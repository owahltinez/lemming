import unittest

from calc import ops


class TestOps(unittest.TestCase):
    def test_add(self):
        self.assertEqual(ops.add(2, 3), 5)

    def test_subtract(self):
        self.assertEqual(ops.subtract(5, 3), 2)

    def test_multiply(self):
        self.assertEqual(ops.multiply(2, 3), 6)
