import unittest


def pr_already_existed(title: str, existing: list[str]) -> bool:
    return title.casefold() in (item.casefold() for item in existing)


class TestPrAlreadyExisted(unittest.TestCase):
    def test_matches_case_insensitively(self):
        self.assertTrue(pr_already_existed("Fix the bug", ["FIX THE BUG"]))
        self.assertTrue(pr_already_existed("fix the bug", ["Fix The Bug"]))

    def test_returns_false_when_no_match(self):
        self.assertFalse(pr_already_existed("New feature", ["Old feature"]))

    def test_returns_false_for_empty_existing_list(self):
        self.assertFalse(pr_already_existed("New feature", []))


if __name__ == "__main__":
    unittest.main()
