def summarize(items: list[str]) -> dict:
    return {"count": len(items), "unique": len(set(items))}
