import unittest


def load_fixture(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if line)


class LoadFixtureTest(unittest.TestCase):
    def test_parses_key_value_lines(self):
        fixture = """name=sample
version=7
enabled=true"""
        result = load_fixture(fixture)
        self.assertEqual(result["name"], "sample")
        self.assertEqual(result["version"], "7")
        self.assertEqual(result["enabled"], "true")


if __name__ == "__main__":
    unittest.main()
