import re
import unittest


def parse_int(value: str, strict: bool = True) -> int | None:
    # Strict mode requires a whole decimal integer; lenient mode reads its leading integer.
    pattern = r"\s*[+-]?\d+\s*" if strict else r"\s*[+-]?\d+"
    match = re.fullmatch(pattern, value) if strict else re.match(pattern, value)
    return int(match.group().strip()) if match else None


class ParseIntTest(unittest.TestCase):
    def test_parse_int(self):
        self.assertEqual(parse_int("  -42  "), -42)
        self.assertIsNone(parse_int("42px"))
        self.assertEqual(parse_int("42px", strict=False), 42)
        self.assertIsNone(parse_int("px42", strict=False))


if __name__ == "__main__":
    unittest.main()
