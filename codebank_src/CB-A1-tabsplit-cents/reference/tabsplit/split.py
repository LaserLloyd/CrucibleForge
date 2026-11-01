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
    if total < 0:
        return {m: -s for m, s in split_evenly(-total, members).items()}
    base, extra = divmod(total, len(members))
    return {m: base + (1 if i < extra else 0) for i, m in enumerate(members)}


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
    if not weights or any(not isinstance(w, int) or isinstance(w, bool) or w <= 0
                          for w in weights.values()):
        raise ValueError("weights must be positive ints")
    if total < 0:
        return {m: -s for m, s in split_by_weights(-total, weights).items()}
    W = sum(weights.values())
    shares = {m: total * w // W for m, w in weights.items()}
    rems = {m: total * w % W for m, w in weights.items()}
    left = total - sum(shares.values())
    order = sorted(weights, key=lambda m: -rems[m])      # stable: ties keep order
    for m in order[:left]:
        shares[m] += 1
    return shares
