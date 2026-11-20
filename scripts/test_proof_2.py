import unittest

from proof_2 import normalize_labels


class NormalizeLabelsTest(unittest.TestCase):
    def test_strips_lowercases_and_deduplicates_in_order(self):
        self.assertEqual(
            normalize_labels([" Bug ", "feature", "bug", " Feature "]),
            ["bug", "feature"],
        )

    def test_empty_labels(self):
        self.assertEqual(normalize_labels([]), [])

    def test_blank_labels_are_preserved_once(self):
        self.assertEqual(normalize_labels([" ", "", "  "]), [""])


if __name__ == "__main__":
    unittest.main()
