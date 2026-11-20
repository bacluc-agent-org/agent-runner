import unittest

from proof_9 import config_value


class ConfigValueTest(unittest.TestCase):
    def test_returns_value_for_existing_key(self):
        self.assertEqual(config_value("mode", {"mode": "safe"}), "safe")

    def test_returns_empty_string_for_missing_key(self):
        self.assertEqual(config_value("missing", {}), "")

    def test_returns_empty_string_when_default_is_empty(self):
        self.assertEqual(config_value("mode", {"mode": ""}), "")


if __name__ == "__main__":
    unittest.main()
