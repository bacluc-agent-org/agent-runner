import unittest

from proof_3 import clamp


class ClampTests(unittest.TestCase):
    def test_default_bounds(self):
        self.assertEqual(clamp(-1), 0)
        self.assertEqual(clamp(50), 50)
        self.assertEqual(clamp(101), 100)

    def test_custom_bounds(self):
        self.assertEqual(clamp(1, 10, 20), 10)
        self.assertEqual(clamp(15, 10, 20), 15)
        self.assertEqual(clamp(25, 10, 20), 20)

    def test_invalid_bounds(self):
        with self.assertRaises(ValueError):
            clamp(15, 20, 10)


if __name__ == "__main__":
    unittest.main()
