"""Month report and year-end report (README "Reports")."""
import math
from collections import defaultdict
from fractions import Fraction

from .dates import month_range, year_range
from .rules import UNCATEGORISED


def by_category(transactions) -> list[tuple[str, int]]:
    spent = defaultdict(int)
    for t in transactions:
        spent[t.category or UNCATEGORISED] -= t.cents
    return sorted(spent.items(), key=lambda kv: (-kv[1], kv[0]))   # spent desc, then name


def month_report(ledger, month: str) -> dict:
    start, end = month_range(month)
    ts = ledger.between(start, end)
    return {"income": sum(t.cents for t in ts if t.cents > 0),
            "spending": -sum(t.cents for t in ts if t.cents < 0),
            "by_category": by_category(ts)}


def _greedy(names, bal, idx):
    """Most negative pays most positive inside one group, ties in household order."""
    out = []
    while True:
        debtor = min(idx, key=lambda i: (bal[i], i))
        creditor = max(idx, key=lambda i: (bal[i], -i))
        if bal[debtor] >= 0 or bal[creditor] <= 0:
            return out
        amount = min(-bal[debtor], bal[creditor])
        out.append((names[debtor], names[creditor], amount))
        bal[debtor] += amount
        bal[creditor] -= amount


def settle(names: list[str], balances: list[int]) -> list[tuple[str, str, int]]:
    """Fewest transfers: as many zero-sum groups as possible, picked canonically (README)."""
    if sum(balances) != 0:
        raise ValueError("balances must add up to 0")
    idx = [i for i, b in enumerate(balances) if b != 0]
    n = len(idx)
    sums = [0] * (1 << n)
    for m in range(1, 1 << n):
        low = (m & -m).bit_length() - 1
        sums[m] = sums[m & (m - 1)] + balances[idx[low]]
    best = {0: 0}

    def most(mask):
        if mask in best:
            return best[mask]
        low = mask & -mask
        rest = mask ^ low
        top = -1
        sub = rest
        while True:
            s = sub | low
            if sums[s] == 0:
                top = max(top, 1 + most(mask ^ s))
            if sub == 0:
                break
            sub = (sub - 1) & rest
        best[mask] = top
        return top

    bal = list(balances)
    out = []
    mask = (1 << n) - 1
    while mask:
        want = most(mask)
        low = mask & -mask
        rest = mask ^ low
        pick = None
        sub = rest
        while True:
            s = sub | low
            if sums[s] == 0 and 1 + most(mask ^ s) == want:
                key = (bin(s).count("1"), [idx[j] for j in range(n) if s >> j & 1])
                if pick is None or key < pick[0]:
                    pick = (key, s)
            if sub == 0:
                break
            sub = (sub - 1) & rest
        out += _greedy(names, bal, pick[0][1])
        mask ^= pick[1]
    return out


def year_end(ledger, household, year: int) -> dict:
    start, end = year_range(year)
    ts = ledger.between(start, end)
    names = household.names
    paid = [0] * len(names)
    exact = [Fraction(0)] * len(names)
    total = 0
    for t in ts:
        if not t.shared:
            continue
        if t.paid_by not in names:
            raise ValueError(f"transaction {t.id}: paid_by {t.paid_by!r} is not a member")
        paid[names.index(t.paid_by)] -= t.cents
        total -= t.cents
        weights = household.weights
        W = sum(weights)
        for i, w in enumerate(weights):
            exact[i] += Fraction(-t.cents * w, W)
    share = [math.floor(x) for x in exact]
    order = sorted(range(len(names)), key=lambda i: (-(exact[i] - share[i]), i))
    for i in order[: total - sum(share)]:
        share[i] += 1
    balance = [p - s for p, s in zip(paid, share)]
    return {"members": [{"name": n, "paid": p, "share": s, "balance": b}
                        for n, p, s, b in zip(names, paid, share, balance)],
            "settle": settle(names, balance),
            "by_category": by_category(ts)}
