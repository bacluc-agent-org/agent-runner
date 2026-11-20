def clamp(value: int, low: int = 0, high: int = 100) -> int:
    if low > high:
        raise ValueError("low must be less than or equal to high")
    return max(low, min(value, high))
