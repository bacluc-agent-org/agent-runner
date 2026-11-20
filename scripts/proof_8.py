import re


def parse_int(value: str, strict: bool = True) -> int | None:
    # Strict mode requires the whole string; lenient mode accepts its leading integer.
    match = re.fullmatch(r"[+-]?\d+", value.strip()) if strict else re.match(r"\s*[+-]?\d+", value)
    return int(match.group()) if match else None


if __name__ == "__main__":
    import unittest

    class TestParseInt(unittest.TestCase):
        def test_parse_int(self):
            self.assertEqual(parse_int("42"), 42)
            self.assertIsNone(parse_int("42x"))
            self.assertEqual(parse_int("42x", strict=False), 42)

    unittest.main()
