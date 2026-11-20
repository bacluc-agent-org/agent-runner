def config_value(key: str, defaults: dict) -> str:
    """Return the configured default, or an empty string when the key is absent."""
    return defaults.get(key, "")
