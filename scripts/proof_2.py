import unittest


def normalize_labels(labels: list[str]) -> list[str]:
    return list(dict.fromkeys(label.strip().lower() for label in labels))


class NormalizeLabelsTest(unittest.TestCase):
    def test_strips_whitespace_lowercases_and_deduplicates_preserving_order(self):
        self.assertEqual(
            normalize_labels(["  Bug ", "FEATURE", "bug", " Feature "]),
            ["bug", "feature"],
        )

    def test_empty_input(self):
        self.assertEqual(normalize_labels([]), [])

    def test_empty_label_is_preserved(self):
        self.assertEqual(normalize_labels(["  ", " Label "]), ["", "label"])


if __name__ == "__main__":
    unittest.main()
