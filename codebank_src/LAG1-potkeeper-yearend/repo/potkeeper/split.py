"""Splitting an amount between members (README "Splitting")."""


def split_cents(total: int, weights: list[int]) -> list[int]:
    """Largest-remainder split of integer cents; the parts add up to total.

    Works on the running total of the weights: member i receives the rounded
    cumulative share up to and including i minus what was handed out before,
    so rounding never accumulates and the last part closes the sum exactly.
    """
    if any(w < 0 for w in weights):
        raise ValueError("weights must be >= 0")
    W = sum(weights)
    if W <= 0:
        raise ValueError("weights need a positive sum")
    parts = []
    done = acc = 0
    for w in weights:
        acc += w
        upto = (2 * total * acc + W) // (2 * W)     # total * acc / W, rounded half up
        parts.append(upto - done)
        done = upto
    return parts
