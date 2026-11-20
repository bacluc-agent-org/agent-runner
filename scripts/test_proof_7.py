import unittest

from proof_7 import load_fixture


class TestLoadFixture(unittest.TestCase):
    def test_parses_key_value_lines(self):
        fixture = "name=agent\nstatus=ready\ncount=3"

        result = load_fixture(fixture)

        self.assertEqual(result["name"], "agent")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["count"], "3")


if __name__ == "__main__":
    unittest.main()
