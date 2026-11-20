def load_fixture(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if line)
