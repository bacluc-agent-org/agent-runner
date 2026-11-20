import unittest


def pr_already_existed(title: str, existing: list[str]) -> bool:
    return title.casefold() in {item.casefold() for item in existing}


class PrAlreadyExistedTest(unittest.TestCase):
    def test_matches_case_insensitively(self):
        self.assertTrue(pr_already_existed("Example PR", ["example pr"]))

    def test_does_not_match_different_titles(self):
        self.assertFalse(pr_already_existed("Example PR", ["Another PR"]))

    def test_does_not_match_empty_existing_list(self):
        self.assertFalse(pr_already_existed("Example PR", []))


if __name__ == "__main__":
    unittest.main()
