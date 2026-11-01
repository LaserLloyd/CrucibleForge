"""Splitting an amount between members (README "Splitting")."""


def split_cents(total: int, weights: list[int]) -> list[int]:
    """Largest-remainder split of integer cents; the parts add up to total."""
    if any(w < 0 for w in weights):
        raise ValueError("weights must be >= 0")
    W = sum(weights)
    if W <= 0:
        raise ValueError("weights need a positive sum")
    if total < 0:
        return [-p for p in split_cents(-total, weights)]
    parts = [total * w // W for w in weights]
    remainders = [(-(total * w % W), i) for i, w in enumerate(weights)]
    missing = total - sum(parts)
    for _, i in sorted(remainders)[:missing]:
        parts[i] += 1
    return parts
