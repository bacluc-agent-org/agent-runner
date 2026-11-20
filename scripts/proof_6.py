import unittest


def is_dirty_marker(path: str) -> bool:
    return path.startswith(".") or path.endswith("~")


class TestIsDirtyMarker(unittest.TestCase):
    def test_dirty_marker_patterns(self):
        self.assertTrue(is_dirty_marker(".hidden"))
        self.assertTrue(is_dirty_marker("backup~"))
        self.assertFalse(is_dirty_marker("clean.txt"))


if __name__ == "__main__":
    unittest.main()
