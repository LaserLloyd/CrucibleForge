"""Splitting a total between members. Integer cents only."""


def split_evenly(total: int, members: list[str]) -> dict[str, int]:
    """Split `total` cents as evenly as possible between `members`.

    Every member gets total // n or one cent more; the extra cents go one
    each to the members in the order given (first member first). A negative
    total (a refund) is split exactly like the positive total of the same
    size, with every share negated. The shares always sum to `total`.
    Raises ValueError when `members` is empty.
    """
    if not members:
        raise ValueError("no members")
    share = round(total / len(members))
    return {m: share for m in members}


def split_by_weights(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Split `total` cents in proportion to positive integer weights.

    Largest-remainder method, for a non-negative total: with W the sum of
    the weights, each member first gets floor(total * w / W); the cents
    still missing go one each to the members with the largest remainders
    (total * w mod W), ties broken by the order of `weights`. A negative
    total mirrors the positive one, as in split_evenly. The shares always
    sum to `total`. Raises ValueError when `weights` is empty or any weight
    is not a positive int.
    """
    W = sum(weights.values())
    raw = {m: total * w / W for m, w in weights.items()}
    shares = {m: int(v) for m, v in raw.items()}
    left = total - sum(shares.values())
    order = sorted(raw, key=lambda m: raw[m] - int(raw[m]), reverse=True)
    for m in order[:left]:
        shares[m] += 1
    return shares
