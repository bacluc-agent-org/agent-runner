"""Provide configuration values with an empty-string fallback."""


def config_value(key: str, defaults: dict) -> str:
    """Return the configured default for key, or an empty string if absent."""
    return defaults.get(key, "")
