import unittest

from proof_10 import summarize


class SummarizeTests(unittest.TestCase):
    def test_summarizes_count_and_unique_cardinality(self):
        self.assertEqual(summarize(["a", "b", "a"]), {"count": 3, "unique": 2})

    def test_empty_items(self):
        self.assertEqual(summarize([]), {"count": 0, "unique": 0})

    def test_all_items_unique(self):
        self.assertEqual(summarize(["a", "b", "c"]), {"count": 3, "unique": 3})
