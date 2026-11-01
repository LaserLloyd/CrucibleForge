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


def settle(names, balances):
    return _greedy(names, list(balances), list(range(len(balances))))


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
        weights = household.weights_on(t.date)
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
