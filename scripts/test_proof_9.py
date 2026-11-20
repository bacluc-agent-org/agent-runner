import unittest

from proof_9 import config_value


class TestConfigValue(unittest.TestCase):
    def test_returns_value_for_existing_key(self):
        self.assertEqual(config_value("region", {"region": "west"}), "west")

    def test_returns_empty_string_for_missing_key(self):
        self.assertEqual(config_value("region", {}), "")

    def test_preserves_empty_string_default(self):
        self.assertEqual(config_value("region", {"region": ""}), "")
