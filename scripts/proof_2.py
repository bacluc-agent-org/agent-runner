def normalize_labels(labels: list[str]) -> list[str]:
    return list(dict.fromkeys(label.strip().lower() for label in labels))
